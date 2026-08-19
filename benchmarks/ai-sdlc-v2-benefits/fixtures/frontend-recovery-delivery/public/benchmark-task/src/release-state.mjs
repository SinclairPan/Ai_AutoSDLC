export function createRiskController(loader, confirmer) {
  const state = {
    risks: [],
    error: null,
    loading: false,
    confirming: new Set(),
  };

  async function load() {
    state.loading = true;
    try {
      state.risks = await loader();
    } catch {
      state.risks = [];
    } finally {
      state.loading = false;
    }
  }

  async function confirm(riskId) {
    const risk = state.risks.find((item) => item.id === riskId);
    if (!risk || risk.confirmed) return;
    await confirmer(riskId);
    risk.confirmed = true;
  }

  return { state, load, confirm };
}
