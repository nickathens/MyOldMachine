import { createApp, h, ref } from 'vue';

createApp({
  setup() {
    const n = ref(0);
    return () => h('button', { onClick: () => n.value++ }, 'n=' + n.value);
  },
}).mount('#app');
