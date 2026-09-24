/** 跨形态共用的格式化工具（Web / 桌面形态都从这里取，勿各写一份）。 */

/**
 * 运行耗时：<1s 显示毫秒（否则 0.04 秒会被 toFixed(1) 显示成 "0.0s"），≥1s 显示两位小数的秒。
 * `duration == null` 才是「没记录」（未结束/拉模式回传的行）；`0` 是合法值，显示 "0ms" 而不是 "-"。
 */
export function durationText(duration: number | null | undefined) {
  if (duration == null) return '-'
  return duration < 1 ? `${(duration * 1000).toFixed(0)}ms` : `${duration.toFixed(2)}s`
}
