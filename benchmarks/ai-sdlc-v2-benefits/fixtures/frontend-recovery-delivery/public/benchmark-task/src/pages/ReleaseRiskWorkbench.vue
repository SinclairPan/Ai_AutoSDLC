<script setup>
import Button from "primevue/button";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import Select from "primevue/select";
import Tag from "primevue/tag";
import { computed, onMounted, reactive, ref } from "vue";

import { createRiskController } from "../release-state.mjs";

const syntheticRisks = [
  {
    id: "RISK-001",
    name: "鉴权回归未完成",
    service: "release-api",
    level: "high",
    owner: "质量团队",
    confirmed: false,
  },
  {
    id: "RISK-002",
    name: "缓存预热窗口不足",
    service: "risk-query",
    level: "medium",
    owner: "平台团队",
    confirmed: false,
  },
];
const controller = createRiskController(
  async () => syntheticRisks,
  async () => undefined,
);
const state = reactive(controller.state);
const selectedLevel = ref("all");
const visibleRisks = computed(() =>
  selectedLevel.value === "all"
    ? state.risks
    : state.risks.filter((risk) => risk.level === selectedLevel.value),
);
onMounted(controller.load);
</script>

<template>
  <main>
    <h1>发布风险工作台</h1>
    <Select v-model="selectedLevel" aria-label="风险等级筛选" />
    <DataTable :value="visibleRisks" data-key="id">
      <Column field="name" header="风险" />
      <Column field="service" header="服务" />
      <Column field="owner" header="负责人" />
      <Column field="level" header="等级"
        ><template #body="{ data }"><Tag :value="data.level" /></template
      ></Column>
      <Column header="操作"
        ><template #body="{ data }"
          ><Button
            label="确认风险"
            :disabled="data.confirmed"
            @click="controller.confirm(data.id)" /></template
      ></Column>
    </DataTable>
  </main>
</template>
