# BB Radio Shelves

[BB Radio](https://github.com/xinjiebi/bb-radio-shelves) 的货架精选池数据仓库。

通过 GitHub Pages 提供动态更新的电台精选池数据，支持客户端热更新（无需发版）。

## 数据地址

| 文件 | URL |
|------|-----|
| 货架索引 | `https://xinjiebi.github.io/bb-radio-shelves/shelves/index.json` |
| 单货架数据 | `https://xinjiebi.github.io/bb-radio-shelves/shelves/{货架名}.json` |
| 健康报告 | `https://xinjiebi.github.io/bb-radio-shelves/health/latest-report.json` |
| 隐私政策 | `https://xinjiebi.github.io/bb-radio-shelves/privacy-policy/` |

## 货架列表

| # | 货架 | 英文名 |
|---|------|--------|
| 1 | 华语流行 | Chinese Pop |
| 2 | 全球热门 | Global Hits |
| 3 | 老歌怀旧 | Oldies |
| 4 | 爵士 | Jazz |
| 5 | 古典 | Classical |
| 6 | 电子 | Electronic |
| 7 | 摇滚 | Rock |
| 8 | 轻音乐 | Easy Listening |
| 9 | 儿歌 | Kids |
| 10 | 助眠 | Sleep |

每个货架固定 10 台电台，共 100 台。

## 自动化

- **每日健康检查**：每天 UTC 6:00 自动检测所有电台流地址可用性，失效台自动替换
- **手动更新**：通过 GitHub Actions 手动触发 `curate.yml` 重新生成精选池

## 客户端集成

客户端启动时从远程拉取 `index.json`，对比本地版本后按需更新：

```
优先级：远程最新 > 本地缓存 > 打包 fallback (assets)
```

## License

Data from [Radio Browser](https://www.radio-browser.info/) (CC license).
