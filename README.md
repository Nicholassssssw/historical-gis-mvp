# Historical GIS MVP

一個可直接部署的研究型 MVP：

**Upload文本 → 可切換 LLM 抽取地名 → 用戶選擇經過/提及 → 用戶確認 → 多來源經緯度配對 → Generate GeoJSON map file → ArcGIS Maps SDK顯示 → 拖動/刪除點 → 路線自動更新。**

## 1. 已實作流程

1. 網站上載 `.txt / .md / .docx / .pdf`，可填寫年份／朝代
2. Backend 抽取文字，顯示總字數及頁數（非 PDF 檔案按字數估算頁數）
3. Vertex AI DeepSeek、Gemini 或 DeepSeek API 經同一個 provider layer 抽取，並回傳固定地名 schema
4. 網頁顯示地名 table；次序、日期、地名及原句只讀，用戶可以：
   - 選擇 `經過`、`提及` 或 `經過及提及`
   - 修改歷史區域
5. 用戶按「確認選擇」後才允許 geocoding；`提及` 地名保留在 table，但不加入配對或路線
6. Geocoder 查詢多個來源並保存所有候選
7. 系統按名稱吻合、來源權重、跨來源距離一致性分為：
   - `confirmed` = 確認經緯度
   - `possible` = 有可能
   - `insufficient` = 資料不足
8. 用戶可從候選下拉選單改用另一個坐標
9. `/map.geojson` 動態生成最新地圖文件
10. ArcGIS Maps SDK for JavaScript 5.1 顯示點及 route
11. Route 由有坐標、active 的點依 `route_order` 即時生成
12. 用戶在 ArcGIS map 用 Sketch 移動一個點：
    - PATCH 新坐標回 database
    - 該點標為 manual confirmed
    - route 即時重畫
13. 用戶刪除點後，前後點自動重連

## 2. 經緯度來源

### Live connectors（已實作）

- **CHGIS / TGAZ**：歷史地名；可傳 name + historical year。
- **Wikidata**：名稱搜尋後讀取 P625 coordinates。
- **OpenStreetMap / Nominatim**：現代/地物 cross-check。
- **Google Places (New)**：設定 `GOOGLE_MAPS_API_KEY` 後啟用；使用 Text Search。

### Local catalog connectors（已實作）

以下來源適合先下載到自己 server，再透過統一 CSV 搜尋：

- **DILA Place Authority**
- **CBDB historical addresses/places**
- **Modern China Geospatial Database (MCGD)**

將資料 normalize 成 `data/README_LOCAL_DATA.md` 所述格式即可自動加入 matching pipeline。

### 預留但未直接猜 API endpoint 的來源

- World Historical Gazetteer (WHG)
- Getty TGN

兩者都有正式 programmatic access；MVP 暫不硬寫尚在演進/需要更精細 reconciliation 的 endpoint。建議下一版獨立加 provider，避免把不穩定接口寫死在核心流程。

## 3. 點解 Map File 用 GeoJSON

系統不需要每次建立 `.gpkg` 再 upload ArcGIS Online。

Database 係 source of truth：

```text
places table
  ↓
GET /api/projects/{id}/map.geojson
  ↓
Point features + one LineString route
```

下載：

```text
/api/projects/1/map.geojson?download=true
```

ArcGIS 網頁顯示則直接由 database JSON 建 GraphicsLayer，拖點後 route 可即時更新。

## 4. 本地啟動

需要 Python 3.12+。

```bash
cp .env.example .env
```

預設使用 Google Vertex AI 的 managed DeepSeek V3.2：

```env
LLM_PROVIDER=google_vertex
LLM_MODEL=deepseek-ai/deepseek-v3.2-maas
GOOGLE_CLOUD_PROJECT=your-project-id
VERTEX_LOCATION=global
```

本機使用 Application Default Credentials：

```bash
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com --project=your-project-id
```

Cloud Run 應使用執行服務帳戶及 IAM，不要把 service-account JSON 放入 repo。

如要切回 Gemini，只需更改設定，不用改程式：

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=...
```

亦保留直連 DeepSeek API 的 `LLM_PROVIDER=deepseek` 選項。

> 注意：Google Cloud 已於 2026-07-21 將 `deepseek-v3.2-maas` 標示為 deprecated，並列出 2026-10-21 retirement date。Provider/model 分離正是為了日後只改環境變數即可遷移。詳見 [Google Cloud open-model deprecations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/deprecations/open-models)。

安裝：

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

瀏覽：

```text
http://127.0.0.1:8000
```

## 5. Google Places

如要加 Google：

```env
GOOGLE_MAPS_API_KEY=...
```

Backend key 不應放在 browser JavaScript。

## 6. ArcGIS

前端使用 **ArcGIS Maps SDK for JavaScript 5.1 CDN**。

可選：

```env
ARCGIS_API_KEY=...
```

有 key 時使用 ArcGIS topographic basemap；無 key 時 MVP 使用 OSM basemap，但 ArcGIS SDK 仍負責 map view、graphics、popup、Sketch editing。

## 7. Production database / hosting

### 本地

預設 SQLite：

```env
DATABASE_URL=sqlite:///./data/app.db
```

### Production 推薦

Supabase / PostgreSQL：

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres
```

Railway：

1. Push repo 去 GitHub
2. Railway → New Project → Deploy from GitHub
3. 設 Environment Variables
4. 如仍用 SQLite，要加 persistent volume mount 到 `/app/data`
5. 正式多人使用建議改 PostgreSQL / Supabase，而不是 Railway ephemeral SQLite

`railway.toml` 同 `Dockerfile` 已包括在 repo。

## 8. Database schema

### projects

保存：文件、原文、年份／朝代、可供地名來源使用的數字年份、workflow stage、是否已由用戶確認地名。

### places

保存：

- route_order
- original_name
- normalized_name
- source sentence
- historical region
- selected lon/lat
- coordinate class
- score/source
- manual override
- active/deleted state

### coordinate_candidates

保存每一個 source 的所有候選，避免只保存最後坐標而失去 provenance。

## 9. API 流程

```text
POST /api/projects
    Upload file

POST /api/projects/{id}/extract
    Gemini extraction

GET/PATCH/DELETE places
    Human review

POST /api/projects/{id}/confirm-places
    Human gate

POST /api/projects/{id}/geocode
    Coordinate matching

POST /api/places/{id}/select-candidate/{candidate_id}
    Human candidate override

GET /api/projects/{id}/map.geojson
    Generate current map file
```

## 10. Matching logic（MVP）

目前 score 係 deterministic，而唔係再叫 Gemini 判坐標：

```text
58% name similarity
42% source reliability
+ cross-source coordinate agreement boost
```

Historical direct match（CHGIS / DILA / CBDB）而名稱高度吻合，或者有多來源坐標在指定半徑內一致，可進入 `confirmed`。其餘按 threshold 分 `possible / insufficient`。

Threshold 可在 `.env` 改：

```env
CONFIRMED_SCORE=0.86
POSSIBLE_SCORE=0.60
AGREEMENT_RADIUS_KM=5
```

**呢個 scoring 只係 MVP 起點。** 下一版應加入：歷史行政區、地物類型、前後 route distance、地方志方位/里數等你現有研究規則。

## 11. 重要限制

- 掃描式 PDF 未包含 OCR；先 OCR 再 upload。
- 長篇書籍第一版會把全文一次交給 extraction stage；production 應做 chunking + deterministic merge，但仍可在 UI 視為「一次抽取流程」。
- 公共 Nominatim 不適合大量 production bulk requests；高流量應 self-host 或用 commercial provider。
- CHGIS 及其他歷史資料庫各有自己的 license；商業化前要逐項確認。
- `confirmed` 代表符合目前機器規則/人工確認，並不等於歷史學上的絕對證明。
- Route 是相鄰坐標的直線骨架，不代表實際古道/河道。之後可以再加入 historical road/hydrology routing。

## 12. 下一版最值得做

1. 把你現有徐霞客人工成果 import 成 gold dataset
2. 加地方志 search + 一次 Gemini gazetteer matching
3. DILA / CBDB / MCGD 正式 ingestion scripts
4. route-context score（前後夾逼）
5. background job queue，避免長文本/geocoding卡住 HTTP request
6. 登入與 project permissions
7. Publish to ArcGIS Online Hosted Feature Layer 作可選 export，而不是核心儲存方式
