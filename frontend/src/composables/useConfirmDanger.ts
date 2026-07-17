// 基线0 业务件 (v1.7.646): 破坏性操作统一确认弹窗
// 规范: 320px 窄档 + 红色确认键 + 必须给具体文案(「删除 40 条交割记录」而非「确定删除吗」)
// 用法: if (await confirmDanger({ title: '清空今日信号', content: '将删除 24 条信号, 不可恢复', confirmText: '清空 24 条' })) { ... }
import { useGlobalDialog } from './useGlobalMessage'

export interface ConfirmDangerOptions {
  title: string
  /** 必须具体: 说清动了什么、多少条、可不可恢复 */
  content: string
  /** 确认键文字, 默认「确认删除」—— 尽量带数量, 如「清空 24 条」 */
  confirmText?: string
}

export function confirmDanger(opts: ConfirmDangerOptions): Promise<boolean> {
  const dialog = useGlobalDialog()
  return new Promise((resolve) => {
    dialog.error({
      title: opts.title,
      content: opts.content,
      positiveText: opts.confirmText || '确认删除',
      negativeText: '取消',
      maskClosable: false,
      style: { width: '320px' },
      positiveButtonProps: { type: 'error' },
      onPositiveClick: () => resolve(true),
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false),
    })
  })
}
