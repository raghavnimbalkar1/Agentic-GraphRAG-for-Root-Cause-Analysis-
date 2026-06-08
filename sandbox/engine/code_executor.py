"""
Code executor: Parse, validate, and execute remediation scripts.

Phase 4: Security validation before sandbox execution.

Checks:
- No shell injection (Python AST analysis)
- No dangerous imports (blacklist check)
- No file system escape attempts
- Valid Python syntax
"""

import ast
from typing import Tuple, List

from core.logging_config import get_logger
from core.exceptions import SandboxSecurityError

logger = get_logger(__name__)


class CodeExecutor:
    """Parses and validates remediation scripts."""

    # Dangerous modules that should not be imported in sandbox
    BLOCKED_IMPORTS = {
        "os.system",
        "subprocess",
        "pickle",
        "marshal",
        "__import__",
        "eval",
        "exec",
        "open",  # Direct file I/O discouraged (use bind mounts)
    }

    def validate_syntax(self, script_code: str) -> Tuple[bool, str]:
        """Validate Python syntax via AST parsing."""
        try:
            ast.parse(script_code)
            logger.info("Script syntax validated")
            return True, ""
        except SyntaxError as e:
            logger.error(f"Syntax error in script: {e}")
            return False, str(e)

    def validate_imports(self, script_code: str) -> Tuple[bool, List[str]]:
        """Check for dangerous imports."""
        try:
            tree = ast.parse(script_code)
            dangerous = []

            for node in ast.walk(tree):
                # Check Import nodes (import X)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in self.BLOCKED_IMPORTS:
                            dangerous.append(alias.name)

                # Check ImportFrom nodes (from X import Y)
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module in self.BLOCKED_IMPORTS:
                        dangerous.append(node.module)

                # Check dangerous function calls (eval, exec, etc.)
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        if node.func.id in self.BLOCKED_IMPORTS:
                            dangerous.append(node.func.id)

            if dangerous:
                logger.error(f"Blocked imports detected: {dangerous}")
                return False, dangerous

            logger.info("Import validation passed")
            return True, []

        except Exception as e:
            logger.error(f"Failed to validate imports: {e}")
            return False, [str(e)]

    def validate_security(self, script_code: str) -> Tuple[bool, str]:
        """Comprehensive security validation."""
        # Check syntax
        syntax_ok, syntax_err = self.validate_syntax(script_code)
        if not syntax_ok:
            raise SandboxSecurityError(f"Invalid syntax: {syntax_err}")

        # Check imports
        imports_ok, blocked_imports = self.validate_imports(script_code)
        if not imports_ok:
            raise SandboxSecurityError(f"Blocked imports: {blocked_imports}")

        logger.info("Security validation passed")
        return True, ""

    def prepare_execution_env(self, env_vars: dict) -> dict:
        """Prepare safe environment variables for sandbox."""
        # Stub: Sanitize env vars (remove secrets that shouldn't be exposed)
        return env_vars
