export const evidenceRows = [
  {
    recordLabel: "Payment: corrected recovery",
    name: "Payment partition",
    handling: 13.448,
    tokens: 786,
    outcome: "Application recovery verified",
    status: "verified",
    overallPassed: true,
  },
  {
    recordLabel: "Redis: persistent-cap fallback",
    name: "Redis persistent-cap fallback",
    handling: 36.357,
    tokens: 1625,
    outcome: "Runtime recovery after restart + flush",
    status: "verified",
    overallPassed: true,
  },
  {
    recordLabel: "CPU: 0.5-CPU cap",
    name: "Adservice CPU cap",
    handling: 13.812,
    tokens: 834,
    outcome: "Mitigation verified; cleanup failed",
    status: "qualified",
    overallPassed: false,
  },
];
