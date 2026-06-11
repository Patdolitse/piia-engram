# 供应链验证

本页说明的发布产物检查自本文档落地后的下一次发布起生效。

## 信任模型

piia-engram 通过 OIDC Trusted Publishing 发布到 PyPI，因此发布工作流不需要长期
PyPI API token。一次发布会依次经过现有的 main 分支祖先关系校验、多道发布门槛、
SBOM hygiene 检查、GitHub artifact attestations，最后发布到 PyPI。

这里的 attestation 指构建溯源证明（attestation）。它说明产物由本仓库该次发布工作流
构建生成。SBOM 指软件物料清单（software bill of materials），由隔离环境生成，该环境
安装的是已经构建出的 wheel。

## 验证构建溯源

下载要检查的 wheel 后运行：

```bash
gh attestation verify piia_engram-<version>-py3-none-any.whl --repo Patdolitse/piia-engram
```

这会验证本地产物对应的构建溯源证明。

## 验证 SBOM attestation

使用同一个 wheel，并要求 CycloneDX 谓词类型：

```bash
gh attestation verify piia_engram-<version>-py3-none-any.whl --repo Patdolitse/piia-engram --predicate-type https://cyclonedx.org/bom
```

这会验证该产物附带 SBOM attestation。

## 获取 SBOM

发布工作流会把 `dist/piia-engram-sbom.cdx.json` 作为名为 `sbom` 的 workflow
artifact 上传，便于人工抽查。GitHub Actions artifact 有保留期，不应视为永久发布资产。

更持久的验证路径是 SBOM attestation；SBOM 会作为 attestation 谓词携带。

## 边界

这些 attestation 只证明一个窄发布事实：产物出自本仓库发布工作流的该次构建。它们不证明
代码或依赖无漏洞，不是第三方安全审计，也不声称构建可复现。
