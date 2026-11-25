# Intel Radar // 個人情報雷達 v0.2 (100% Cloud + 30-Year Funnel Chronicle)

線上看板網址：**[https://jojoey-spec.github.io/intel-radar/](https://jojoey-spec.github.io/intel-radar/)**

## 三大核心模組

1. **🚨 世界重大情報（不夠大條絕不吵你）**
   - 聚合 22 個全球政經、財經、科技與基礎科學權威信源（含 BBC、NYT、CNBC、TechMeme、Nature、Quanta Magazine、New Scientist 與 Hacker News 5日 >800分核彈榜）。
   - 僅 **Lv.9–10** 核彈級劇變主動推播（並自動沉澱進歷史大事時間軸）；**Lv.7–8** 靜音收錄；日常促銷與雜訊自動攔截。

2. **🕵️ 小道消息傳言版（33% / 67% 三欄證據計分制）**
   - 依客觀證據指標（硬證據流出 / 爆料紀錄 / 多方交叉印證 / 細節具體度）換算可信度，分為 **可能性極高 (68–100%)**、**待觀察 (34–67%)**、**不太可信 (0–33%)**，支援自動升檔通知。

3. **📜 歷史大事座標（方案 A：漏斗濃縮式 1 / 5 / 10 / 30 年）**
   - **過去 1 年**：Lv.8.5+ 年度大事（並與雲端即時抓到的核彈新聞自動連動沉澱）
   - **過去 5 年**：Lv.9.0+ 五年格局劇變
   - **過去 10 年**：Lv.9.5+ 十年時代轉折
   - **過去 30 年**：Lv.10 教科書級歷史座標（1996–2026）
   - 完整支援上方領域按鈕交叉篩選（例如一鍵看「過去 30 年 × 半導體・晶片」或「過去 30 年 × 科學・數學突破」）。

## 全雲端免費自動運行（GitHub Actions + GitHub Models）

透過 `.github/workflows/radar.yml` 每 **20 分鐘**在 GitHub 免費算力上執行 `python3 -m backend.cloud_runner`：
- 採用 **整批打包（Batch Prompting）** 呼叫免費內建的 GitHub Models (`openai/gpt-4o-mini`)，每輪最多僅消耗 1 次請求（無新事件時消耗 0 次），完全無需開啟個人電腦。
- 如需串接手機推播，可在 GitHub Repo `Settings -> Secrets and variables -> Actions` 新增 `NTFY_TOPIC`、`BARK_URL` 或 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`。
