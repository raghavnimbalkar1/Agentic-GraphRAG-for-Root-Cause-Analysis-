"""
Agent tool layer.

sandbox_tools.execute_sop() is the only tool the agent invokes: it runs a
remediation script inside an isolated, capability-dropped Docker container with
per-procedure privilege scoping. The script path always comes from the Neo4j
Skill record, never from model output.
"""
