/** 安全渲染模型Markdown，原始HTML禁用，链接校验沿用markdown-it安全规则。 */
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true })
markdown.set({
  /** 对已声明的语言做转义高亮，未知语言仍转义输出。 */
  highlight(source: string, language: string): string {
    return language && hljs.getLanguage(language) ? hljs.highlight(source, { language, ignoreIllegals: true }).value : markdown.utils.escapeHtml(source)
  },
})
const originalFence = markdown.renderer.rules.fence!
/** 复制按钮只包含固定HTML，代码正文仍通过安全渲染器。 */
markdown.renderer.rules.fence = (tokens, index, options, environment, renderer) => '<div class="code-block"><button type="button" data-copy-code>复制代码</button>' + originalFence(tokens, index, options, environment, renderer) + '</div>'
/** 返回已经禁用原始HTML并过滤危险协议的Markdown。 */
export function renderMarkdown(text: string): string { return markdown.render(text) }
