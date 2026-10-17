# llm-recommendation-system
FastAPI ve Google Gemini API kullanılarak geliştirilmiş, çok kriterli anime öneri ve puanlama.

## Özellikler
- **Doğal Dil Anlama (NLU):** Gemini API kullanılarak kullanıcı metinlerinden olumlu/olumsuz etiket ve tema çıkarımı.
- **Dinamik Veri Çekme:** AniList GraphQL API üzerinden filtreli veri sorgulama.
- **Özel Skorlama Algoritması:** Eşleşen tür, etiket ve ana temalara göre dinamik ağırlıklı puanlama.
- **Loglama:** Oturum ve arama analizleri için JSONL formatında veri takibi.

## Kullanılan Teknolojiler
- **Backend:** Python, FastAPI, Pydantic, HTTPX
- **Yapay Zeka & API:** Google GenAI SDK (Gemini), AniList GraphQL API
- **Frontend:** HTML5, TailwindCSS, Vanilla JS
