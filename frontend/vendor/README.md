# 浏览器依赖

| 目录 | 版本 | 来源 | 许可 |
| --- | --- | --- | --- |
| vue | 3.5.43 | npm `vue` 浏览器发行文件 | MIT |
| element-plus | 2.14.7 | npm `element-plus`，按需打包 | MIT |
| icons-vue | 2.3.2 | npm `@element-plus/icons-vue`，随 Element Plus 按需打包 | MIT |
| marked | 9.1.2 | npm `marked`，沿用现有 Markdown 解析版本 | MIT |
| dompurify | 3.4.16 | npm `dompurify` | Apache-2.0 / MPL-2.0 |

各目录包含上游许可文本。Vue、Marked、DOMPurify 和 Element Plus 暗色变量使用上游发行文件。字体来源说明独立位于 `assets/fonts/README.md`。

页面新增 Element Plus 组件或图标时，更新仓库根目录下的 `tools/frontend-vendor/components.js` 中的导入。在该目录执行：

```sh
npm ci
npm run build
```

提交生成的 `element-plus/components.js` 和 `element-plus/components.css`。应用运行和 Docker 构建直接使用仓库中的文件。
