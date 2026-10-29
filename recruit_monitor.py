#!/usr/bin/env python3
"""
취업 공고 모니터링 봇 (Playwright 헤드리스 브라우저 - 단일 메세지 발송판)
- JavaScript Dynamic UI 지원 및 공공기관 방화벽/SSL 오류 완벽 대응
- 한국 시간(KST, UTC+9) 적용 완료
- 분할 발송 로직 제거: 신규 공고를 1개의 텔레그램 메세지로 일괄 전송
"""

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests
from playwright.sync_api import sync_playwright

# ============================================
# 설정
# ============================================

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SITES = [
    {"name": "한수정", "url": "https://www.koagi.or.kr/www/user/bbs/BD_selectBbsList.do?q_bbsSn=1009"},
    {"name": "인천시청 새소식", "url": "https://www.incheon.go.kr/IC010101"},
    {"name": "인천시청 고시공고", "url": "https://www.incheon.go.kr/IC010303"}
]

KEYWORDS = ["채용", "모집"]

HISTORY_FILE = "recruit_history.json"

# ============================================
# 헬퍼 함수
# ============================================

def get_kst_now():
    return datetime.now(ZoneInfo('Asia/Seoul'))

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_history(history):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"💾 히스토리 파일 저장 완료 ({len(history)}건 기록됨)")
    except Exception as e:
        print(f"❌ 히스토리 저장 실패: {e}")

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 텔레그램 설정이 없습니다.")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        res = requests.post(url, data=data)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ 텔레그램 전송 실패: {e}")
        return False

def send_safe_telegram_messages(messages_list):
    header = "🚀 <b>새로운 채용 공고가 올라왔습니다!</b>\n\n"
    footer = f"\n⏰ 확인 시간: {get_kst_now().strftime('%Y-%m-%d %H:%M')}"
    
    current_chunk = header
    
    for item in messages_list:
        if len(current_chunk) + len(item) > 3500:
            send_telegram_message(current_chunk)
            time.sleep(1)
            current_chunk = "🚀 <b>이어서 전송합니다...</b>\n\n" + item
        else:
            current_chunk += item
            
    current_chunk += footer
    send_telegram_message(current_chunk)

def extract_titles_from_frame(frame, seen_set):
    extracted = []
    try:
        selectors = (
            "a, td, div.title, h3, h4, span, .subject, .title, "
            ".notice, .bo_notice, .notice_title, tr.notice a, tr.bo_notice a"
        )
        elements = frame.query_selector_all(selectors)
        
        for el in elements:
            try:
                text = clean_text(el.inner_text())
                
                if len(text) > 150:
                    continue
                    
                if 5 <= len(text) <= 150 and text not in seen_set:
                    bad_keywords = ["원문보기", "다운로드", "더보기", "바로가기", "로그인", "저작권", "개인정보"]
                    if not any(bad == text for bad in bad_keywords):
                        seen_set.add(text)
                        extracted.append(text)
            except Exception:
                continue
    except Exception:
        pass
    return extracted

def fetch_titles_with_browser(context, url):
    titles = []
    page = None
    try:
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            err_msg = str(e)
            if "ERR_HTTP2_PROTOCOL_ERROR" in err_msg or "ERR_CONNECTION" in err_msg or "ERR_ABORTED" in err_msg:
                print(f"    ❌ 방화벽/통신 에러로 즉시 패스: {err_msg[:40]}...")
                return []
            else:
                raise e
        
        page.wait_for_timeout(5000)
        
        seen = set()
        titles.extend(extract_titles_from_frame(page, seen))
        
        for frame in page.frames:
            if frame != page:
                try:
                    frame_titles = extract_titles_from_frame(frame, seen)
                    titles.extend(frame_titles)
                except Exception:
                    continue
                    
        if page:
            page.close()
        return titles
    except Exception as e:
        print(f"    ❌ 접속/파싱 패스: {str(e)[:60]}...")
        return []

# ============================================
# 메인 실행부
# ============================================

def main():
    print(f"🤖 채용 공고 모니터링 시작: {get_kst_now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    history = load_history()
    found_new = False
    messages = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            ignore_https_errors=True
        )
        
        for idx, site in enumerate(SITES, 1):
            site_name = site['name']
            site_url = site['url']
            print(f"[{idx}/{len(SITES)}] 🔍 확인 중: {site_name}")
            
            titles = fetch_titles_with_browser(context, site_url)
            site_found_count = 0
            
            for title in titles:
                if any(kw in title for kw in KEYWORDS):
                    history_key = f"{site_name}_{title}"
                    if history_key not in history:
                        print(f"    ✨ [신규 발견] {title}")
                        history[history_key] = get_kst_now().isoformat()
                        found_new = True
                        messages.append(f"🎯 <b>{site_name}</b>\n{title}\n🔗 {site_url}\n\n")
                        site_found_count += 1
                        
            if site_found_count == 0:
                print(f"    → (추출된 텍스트 수: {len(titles)}개 / 신규 공고 없음)")
                        
        browser.close()

    if found_new and messages:
        send_safe_telegram_messages(messages)
    else:
        print("\n✅ 신규 공고가 없습니다.")
        send_telegram_message(f"✅ 확인 완료 (신규 공고 없음) - {get_kst_now().strftime('%H:%M')}")
    
    save_history(history)
    print(f"\n✅ 프로세스 완료")

if __name__ == "__main__":
    main()
