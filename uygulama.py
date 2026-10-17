import os
import json
import httpx
import time
import aiofiles
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from contextlib import asynccontextmanager

# =====================================================================
# 1. ORTAM DEĞİŞKENLERİ VE GÜVENLİK
# =====================================================================
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("HATA: GEMINI_API_KEY ortam değişkeni bulunamadı.")

client = genai.Client(api_key=api_key)

IP_LOGS = {}          
DAILY_IP_COUNTS = {}  
CURRENT_DAY = time.strftime("%Y-%m-%d")

ARAMA_LOG_DOSYASI = "1_arama_loglari.jsonl"
REAKSIYON_LOG_DOSYASI = "2_anime_reaksiyon_loglari.jsonl"
GENEL_DEGERLENDIRME_DOSYASI = "3_genel_degerlendirme_loglari.jsonl"

# =====================================================================
# 2. HTTPX İSTEMCİ YÖNETİMİ
# =====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    yield
    await app.state.http_client.aclose()

app = FastAPI(title="Gelişmiş Anime Öneri API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# 3. VERİ ŞEMALARI
# =====================================================================
class TamOtomatikIstek(BaseModel):
    kullanici_metni: str = Field(..., min_length=3, max_length=500)
    session_id: str = Field(...)
    kesin_turler: list[str] = Field(default_factory=list, max_length=3)
    benzer_anime_türleri: list[str] = Field(default_factory=list)

class ReaksiyonIstek(BaseModel):
    session_id: str
    anime_adi: str
    durum: str = Field(..., description="'begendim', 'izledim_gec', 'begenmedim'")

class GenelDegerlendirmeIstek(BaseModel):
    session_id: str
    puan: int = Field(..., ge=1, le=5)
    geri_bildirim_metni: str = Field(default="")

class AnimeAnalizSemasi(BaseModel):
    olumlular: list[str]
    olumsuzlar: list[str]
    ana_tema: str = Field(default="", description="Kullanıcının metnindeki en baskın spesifik tema (İngilizce tag olarak, örn: Gothic, Cyberpunk, Death Game). Yoksa boş bırak.")

# =====================================================================
# 4. ANILIST VE PUANLAMA MOTORU (Resim Çekme Eklendi)
# =====================================================================
async def anilistten_akilli_anime_bul(http_client: httpx.AsyncClient, ai_olumlular: list, ai_olumsuzlar: list, kesin_turler: list, benzer_turler: list, ana_tema: str):
    url = "https://graphql.anilist.co"
    ANILIST_TURLERI = ["Action", "Adventure", "Comedy", "Drama", "Ecchi", "Fantasy", "Horror", 
                       "Mahou Shoujo", "Mecha", "Music", "Mystery", "Psychological", 
                       "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller"]
    
    aktif_kesin_turler = [k for k in kesin_turler if k in ANILIST_TURLERI]
    yasak_turler = [k for k in ai_olumsuzlar if k in ANILIST_TURLERI]
    yasak_tagler = [k for k in ai_olumsuzlar if k not in ANILIST_TURLERI]

    query = """
    query ($genres: [String], $notGenres: [String], $notTags: [String]) {
      Page(page: 1, perPage: 100) {
        media(genre_in: $genres, genre_not_in: $notGenres, tag_not_in: $notTags, type: ANIME, format: TV, isAdult: false) {
          title {
            romaji
            english
          }
          averageScore
          genres
          coverImage {
            large
          }
          tags {
            name
          }
          description
        }
      }
    }
    """
    
    ham_animeler = []
    senaryolar = []
    
    if len(aktif_kesin_turler) == 3:
        senaryolar.append(aktif_kesin_turler)
        senaryolar.append(aktif_kesin_turler[:2])
        senaryolar.append([aktif_kesin_turler[0]])
    elif len(aktif_kesin_turler) == 2:
        senaryolar.append(aktif_kesin_turler)
        senaryolar.append([aktif_kesin_turler[0]])
    elif len(aktif_kesin_turler) == 1:
        senaryolar.append(aktif_kesin_turler)
        
    if ai_olumlular:
        gecerli_ai_turleri = [k for k in ai_olumlular if k in ANILIST_TURLERI]
        if gecerli_ai_turleri:
            senaryolar.append([gecerli_ai_turleri[0]])
            
    senaryolar.append(["Action"])

    for filtre_türleri in senaryolar:
        degiskenler = {"genres": filtre_türleri}
        if yasak_turler: degiskenler["notGenres"] = yasak_turler
        if yasak_tagler: degiskenler["notTags"] = yasak_tagler
        
        try:
            cevap = await http_client.post(url, json={"query": query, "variables": degiskenler})
            if cevap.status_code == 200:
                ham_animeler = cevap.json().get("data", {}).get("Page", {}).get("media", [])
                if ham_animeler:
                    break
        except Exception:
            continue

    if not ham_animeler:
        return []

    puanlanmis_havuz = []
    yasakli_ekler = ["2nd season", "season 2", "3rd season", "season 3", "4th season", "season 4", "part 2", "part 3"]

    for anime in ham_animeler:
        romaji_baslik = (anime["title"].get("romaji") or "").lower()
        english_baslik = (anime["title"].get("english") or "").lower()
        
        if any(ek in romaji_baslik or ek in english_baslik for ek in yasakli_ekler):
            continue

        base_score = 0
        anime_turleri = anime.get("genres", [])
        anime_tagleri = [t["name"].lower() for t in anime.get("tags", [])]

        for etiket in ai_olumlular:
            if etiket in anime_turleri or etiket.lower() in anime_tagleri:
                base_score += 3

        for b_tur in benzer_turler:
            if b_tur in anime_turleri:
                base_score += 2

        if anime.get("averageScore", 0) >= 80:
            base_score += 1

        if ana_tema and ana_tema.lower() in anime_tagleri:
            base_score += 15

        temiz_ozet = anime.get("description", "Açıklama bulunamadı.")
        if temiz_ozet:
            for html_tag in ["<br>", "<br />", "<i>", "</i>", "<b>", "</b>"]:
                temiz_ozet = temiz_ozet.replace(html_tag, "")

        puanlanmis_havuz.append({
            "title": anime["title"],
            "averageScore": anime["averageScore"],
            "genres": anime_turleri,
            "description": temiz_ozet[:300] + "...",
            "resim": anime.get("coverImage", {}).get("large", ""),
            "algoritma_puani": base_score,
            "ana_tema_eslesmesi": bool(ana_tema and ana_tema.lower() in anime_tagleri)
        })

    puanlanmis_havuz.sort(key=lambda x: x["algoritma_puani"], reverse=True)
    return puanlanmis_havuz[:20]

# =====================================================================
# 5. API ROTALARI
# =====================================================================
@app.post("/tam-otomatik-oner")
async def tam_otomatik_oner(istek: TamOtomatikIstek, request: Request):
    global CURRENT_DAY
    istemci_ip = request.client.host
    su_an = time.time()

    yeni_gun = time.strftime("%Y-%m-%d")
    if yeni_gun != CURRENT_DAY:
        DAILY_IP_COUNTS.clear()
        CURRENT_DAY = yeni_gun

    ip_bugun_kac_yapti = DAILY_IP_COUNTS.get(istemci_ip, 0)
    if ip_bugun_kac_yapti >= 30:
        raise HTTPException(status_code=403, detail="Günlük arama limitine ulaştınız.")

    if istemci_ip in IP_LOGS:
        son_istek_zamani = IP_LOGS[istemci_ip]
        kalan_sure = 60.0 - (su_an - son_istek_zamani)
        if kalan_sure > 0:
            raise HTTPException(status_code=429, detail=f"Lütfen {int(kalan_sure)} saniye bekleyin.")

    system_instruction = (
        "Sen üst düzey bir anime uzmanı ve veri analizcisisin. "
        "Kullanıcının metnini analiz edip AniList İngilizce etiketlerini bul. "
        "İstediklerini 'olumlular', istemediklerini 'olumsuzlar' listesine koy. "
        "Kullanıcı spesifik bir alt kültür veya tema belirttiyse (örn: Gothic, Cyberpunk), bunu 'ana_tema' alanına tek kelime İngilizce olarak yaz."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Kullanıcı İsteği: {istek.kullanici_metni}",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AnimeAnalizSemasi,
                temperature=0.1
            )
        )
        
        yapay_zeka_paket = json.loads(response.text)
        ana_tema_verisi = yapay_zeka_paket.get("ana_tema", "")
        
        canli_yirmilik_liste = await anilistten_akilli_anime_bul(
            request.app.state.http_client, 
            yapay_zeka_paket.get("olumlular", []), 
            yapay_zeka_paket.get("olumsuzlar", []),
            istek.kesin_turler,
            istek.benzer_anime_türleri,
            ana_tema_verisi
        )

        if not canli_yirmilik_liste:
            raise HTTPException(status_code=404, detail="Uygun anime bulunamadı.")

        IP_LOGS[istemci_ip] = su_an
        DAILY_IP_COUNTS[istemci_ip] = ip_bugun_kac_yapti + 1

        log_verisi = {
            "timestamp": su_an,
            "session_id": istek.session_id,
            "gelen_metin": istek.kullanici_metni,
            "frontend_kesin_turler": istek.kesin_turler,
            "ai_ne_anladi": yapay_zeka_paket,
            "verilen_anime_sayisi": len(canli_yirmilik_liste)
        }
        async with aiofiles.open(ARAMA_LOG_DOSYASI, mode='a', encoding='utf-8') as f:
            await f.write(json.dumps(log_verisi, ensure_ascii=False) + "\n")
        
        return {
            "yapay_zekanin_analizi": yapay_zeka_paket,
            "reroll_havuzu_toplam_20_anime": canli_yirmilik_liste
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sistem Hatası: {str(e)}")

@app.post("/reaksiyon-kaydet")
async def reaksiyon_kaydet(istek: ReaksiyonIstek):
    try:
        veri = {"timestamp": time.time(), "session_id": istek.session_id, "anime": istek.anime_adi, "aksiyon": istek.durum}
        async with aiofiles.open(REAKSIYON_LOG_DOSYASI, mode='a', encoding='utf-8') as f:
            await f.write(json.dumps(veri, ensure_ascii=False) + "\n")
        return {"durum": "kaydedildi"}
    except Exception:
        raise HTTPException(status_code=500, detail="Log hatası.")

@app.post("/genel-degerlendirme")
async def genel_degerlendirme_kaydet(istek: GenelDegerlendirmeIstek):
    try:
        veri = {"timestamp": time.time(), "session_id": istek.session_id, "puan": istek.puan, "yorum": istek.geri_bildirim_metni}
        async with aiofiles.open(GENEL_DEGERLENDIRME_DOSYASI, mode='a', encoding='utf-8') as f:
            await f.write(json.dumps(veri, ensure_ascii=False) + "\n")
        return {"durum": "kaydedildi"}
    except Exception:
        raise HTTPException(status_code=500, detail="Log hatası.")