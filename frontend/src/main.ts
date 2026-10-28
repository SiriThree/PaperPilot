import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import './home-shell.css'
import './business-workspace.css'
import App from './App.vue'
import { router } from './router'

createApp(App).use(router).use(ElementPlus).mount('#app')
