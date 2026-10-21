// Frontmatter dates arrive as Date objects or strings; posts compare as YYYY-MM-DD.
export const isoDate = (value: unknown) => new Date(value as string).toISOString().slice(0, 10)

export const formatDate = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number)
  return `${y} 年 ${m} 月 ${d} 日`
}
