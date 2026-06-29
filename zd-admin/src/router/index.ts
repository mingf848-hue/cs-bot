import { createRouter, createWebHashHistory } from 'vue-router'
import type { App } from 'vue'
import { Layout } from '@/utils/routerHelper'

export const constantRouterMap: AppRouteRecordRaw[] = [
  {
    path: '/',
    component: Layout,
    redirect: '/dashboard',
    name: 'Root',
    meta: {
      hidden: true
    }
  },
  {
    path: '/login',
    component: () => import('@/views/Login/Login.vue'),
    name: 'Login',
    meta: {
      hidden: true,
      title: '登录',
      noTagsView: true
    }
  },
  {
    path: '/404',
    component: () => import('@/views/Error/404.vue'),
    name: 'NoFind',
    meta: {
      hidden: true,
      title: '404',
      noTagsView: true
    }
  }
]

export const asyncRouterMap: AppRouteRecordRaw[] = [
  {
    path: '/dashboard',
    component: Layout,
    name: 'Dashboard',
    meta: {
      title: '监控台',
      icon: 'vi-ant-design:dashboard-filled',
      affix: true
    },
    children: [
      {
        path: '',
        component: () => import('@/views/Zd/Dashboard.vue'),
        name: 'ZdDashboard',
        meta: {
          title: '监控台',
          affix: true
        }
      }
    ]
  },
  {
    path: '/rules',
    component: Layout,
    name: 'Rules',
    meta: {
      title: '规则管理',
      icon: 'vi-ant-design:control-filled'
    },
    children: [
      {
        path: '',
        component: () => import('@/views/Zd/Rules.vue'),
        name: 'ZdRules',
        meta: {
          title: '规则管理'
        }
      }
    ]
  },
  {
    path: '/resources',
    component: Layout,
    name: 'Resources',
    meta: {
      title: '资源管理',
      icon: 'vi-ant-design:database-filled'
    },
    children: [
      {
        path: '',
        component: () => import('@/views/Zd/Resources.vue'),
        name: 'ZdResources',
        meta: {
          title: '资源管理'
        }
      }
    ]
  },
  {
    path: '/scheduled',
    component: Layout,
    name: 'Scheduled',
    meta: {
      title: '定时任务',
      icon: 'vi-ant-design:clock-circle-filled'
    },
    children: [
      {
        path: '',
        component: () => import('@/views/Zd/Scheduled.vue'),
        name: 'ZdScheduled',
        meta: {
          title: '定时任务'
        }
      }
    ]
  },
  {
    path: '/settings',
    component: Layout,
    name: 'Settings',
    meta: {
      title: '系统设置',
      icon: 'vi-ant-design:setting-filled'
    },
    children: [
      {
        path: '',
        component: () => import('@/views/Zd/Settings.vue'),
        name: 'ZdSettings',
        meta: {
          title: '系统设置'
        }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  strict: true,
  routes: constantRouterMap as any,
  scrollBehavior: () => ({ left: 0, top: 0 })
})

export const resetRouter = (): void => {
  const resetWhiteNameList = ['Redirect', 'RedirectWrap', 'Login', 'NoFind', 'Root']
  router.getRoutes().forEach((route) => {
    const { name } = route
    if (name && !resetWhiteNameList.includes(name as string)) {
      router.hasRoute(name) && router.removeRoute(name)
    }
  })
}

export const setupRouter = (app: App<Element>) => {
  app.use(router)
}

export default router
