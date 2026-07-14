export function routeIncidents(incidents) {
  return incidents.map((incident) => {
    const highRisk = incident.severity === "high";
    const queue = highRisk ? "review" : "triage";
    const reason = highRisk ? `${incident.severity}:${incident.category}-review` : "triage";

    return {
      id: incident.id,
      queue,
      reason
    };
  });
}
