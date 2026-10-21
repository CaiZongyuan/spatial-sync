import { createContentLoader } from 'vitepress'

// Every post is a Markdown file in blog/ whose frontmatter has a date.
export interface Post {
  url: string
  title: string
  date: string
  description: string
  cover?: string
  author?: string
}

declare const data: Post[]
export { data }

export default createContentLoader('blog/*.md', {
  transform(raw): Post[] {
    return raw
      .filter((page) => page.frontmatter.date)
      .map((page) => ({
        url: page.url,
        title: page.frontmatter.title,
        date: new Date(page.frontmatter.date).toISOString().slice(0, 10),
        description: page.frontmatter.description ?? '',
        cover: page.frontmatter.cover,
        author: page.frontmatter.author,
      }))
      .sort((a, b) => b.date.localeCompare(a.date))
  },
})
