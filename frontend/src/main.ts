/** 初始化Vue、Pinia、路由及Element Plus。 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import './styles.css'
import App from './App.vue'
import router from './router'

/** 挂载使用中文组件文案的组织管理控制台。 */
createApp(App).use(createPinia()).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
