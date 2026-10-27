<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 })
const paths: Record<string, string[]> = {
  book: ['M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4z', 'M13 7a3 3 0 0 1 3-3h4v15h-3a4 4 0 0 0-4 2'],
  grid: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
  folder: ['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  search: ['M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14', 'm16 16 5 5'],
  shield: ['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z', 'm8 12 3 3 5-6'],
  arrow: ['M4 12h16', 'm14 6 6 6-6 6'],
  chevron: ['m9 5 7 7-7 7'],
  plus: ['M12 5v14', 'M5 12h14'],
  refresh: ['M20 7v5h-5', 'M4 17v-5h5', 'M6 7a7 7 0 0 1 12-1l2 3', 'm4 15 2 3a7 7 0 0 0 12-1'],
  people: ['M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8', 'M2 21v-3a7 7 0 0 1 14 0v3', 'M17 4a4 4 0 0 1 0 8', 'M20 21v-3a7 7 0 0 0-3-5'],
  file: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6', 'M8 12h8', 'M8 16h6'],
  check: ['m5 12 4 4L19 6'],
  logout: ['M9 4H4v16h5', 'M9 12h12', 'm17 8 4 4-4 4'],
  menu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  info: ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20', 'M12 11v6', 'M12 7h.01'],
  quote: ['M10 5H3v8h5a5 5 0 0 1-5 5', 'M21 5h-7v8h5a5 5 0 0 1-5 5'],
  upload: ['M12 16V3', 'm7 8 5-5 5 5', 'M4 15v5h16v-5'],
  filter: ['M3 4h18l-7 8v7l-4 2v-9z'],
  save: ['M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2', 'M7 3v6h10V3', 'M7 21v-8h10v8'],
  clock: ['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20', 'M12 6v6l4 2'],
}
</script>
<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false" class="app-icon">
    <path v-for="(path, index) in paths[name] || paths.file" :key="index" :d="path" />
  </svg>
</template>
