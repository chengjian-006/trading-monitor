import { ref, type Ref } from 'vue'

/**
 * 防重复提交守卫 (v1.7.726)
 *
 * 背景: 全站写操作触发点约 72 处, 其中 15 处完全没有在途保护 —— 因为项目里一直没有通用封装,
 * 各页面各写各的 `loading` / `saving` / `xxxBusy` 标志位(约 30 个, 命名用法都不统一),
 * 全靠人肉记得写, 于是漏掉一批。本 composable 就是补上这个缺失的通用件。
 *
 * 与 api/client.ts 里那层「写请求在途去重」的分工:
 *   - client.ts 那层按 `方法+URL+请求体` 判重, 是【全局兜底】, 拦的是完全相同的并发重复请求
 *     (连点两次「添加」、重复导入同一份文件)。
 *   - 但它拦不住【开关型】操作 —— 快速切换 持仓/关注/预警启停 时, 每次请求体都不同
 *     (true → false → true), 判重不命中, 问题也不是"重复"而是"乱序返回导致最终状态与预期相反"。
 *     这类必须在按钮侧挡住第二次点击, 就是本文件的职责。
 *
 * 用法:
 *   const { busy, guard } = useSubmitGuard()
 *   const onSave = guard(async () => { await api.save(...) })
 *   <NButton :loading="busy" @click="onSave">保存</NButton>
 *
 * 多目标场景(表格逐行操作, 只想禁用被点的那一行)用 useKeyedSubmitGuard。
 */
export function useSubmitGuard() {
  const busy = ref(false)

  /** 包住异步处理器: 在途时直接早退, 结束(无论成败)复位。返回值透传。 */
  function guard<A extends unknown[], R>(
    fn: (...args: A) => Promise<R>,
  ): (...args: A) => Promise<R | undefined> {
    return async (...args: A) => {
      if (busy.value) return undefined
      busy.value = true
      try {
        return await fn(...args)
      } finally {
        // 必须 finally 复位: 失败也要放开, 否则一次报错就把按钮永久锁死
        busy.value = false
      }
    }
  }

  return { busy, guard }
}

/**
 * 按 key 分别守卫 —— 表格里逐行的开关/删除, 点第 3 行不该把第 5 行也禁用。
 *
 * 用法:
 *   const { isBusy, guardKey } = useKeyedSubmitGuard()
 *   const onToggle = guardKey((row) => row.code, async (row) => { await api.update(row) })
 *   <NSwitch :loading="isBusy(row.code)" @update:value="onToggle(row)" />
 */
export function useKeyedSubmitGuard() {
  const busyKeys: Ref<Set<string>> = ref(new Set())

  const isBusy = (key: string) => busyKeys.value.has(key)

  // keyOf 用 NoInfer 包住: 让泛型 A 只从 fn 推断。
  // 否则 A 会被 keyOf 先推断掉 —— 当处理器参数比取键函数多时(常见: fn 是 (task, enabled) => ...,
  // 而取键只需要 (task) => task.job_id), A 被推窄成 [task], 调用处传两个实参就 TS2554,
  // 逼得每个调用方都得把用不到的参数补进取键函数签名里。缺陷应该在公共件修, 不该让调用方将就。
  function guardKey<A extends unknown[], R>(
    keyOf: (...args: NoInfer<A>) => string,
    fn: (...args: A) => Promise<R>,
  ): (...args: A) => Promise<R | undefined> {
    return async (...args: A) => {
      const key = keyOf(...args)
      if (busyKeys.value.has(key)) return undefined
      // Set 原地 add 不触发 Vue 响应式, 必须换新引用
      busyKeys.value = new Set(busyKeys.value).add(key)
      try {
        return await fn(...args)
      } finally {
        const next = new Set(busyKeys.value)
        next.delete(key)
        busyKeys.value = next
      }
    }
  }

  return { isBusy, guardKey, busyKeys }
}
