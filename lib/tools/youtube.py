"""
youtube.py

YouTube Data API v3 로 쇼츠 업로드. 무료 (2026-06 부터 업로드 전용 쿼터 하루 100회).

준비 (1회):
  1. Google Cloud Console 에서 프로젝트 생성 → YouTube Data API v3 사용 설정
  2. OAuth 클라이언트 ID (데스크톱 앱) 생성 → client_secret.json 다운로드
  3. 환경변수 YOUTUBE_CLIENT_SECRET=<client_secret.json 경로>
  4. 첫 실행 때 브라우저가 열려 채널 권한을 승인하면 secrets/youtube_token.json 에 토큰 저장

쇼츠로 인식되는 조건: 세로 9:16, 60초 이하, 제목이나 설명에 #Shorts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TOKEN_PATH = Path("secrets/youtube_token.json")


class YouTubeError(RuntimeError):
    pass


def _service(token_path: Path = DEFAULT_TOKEN_PATH):
    # 무거운 google 라이브러리는 실제 업로드 때만 import
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds: Optional[Credentials] = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
            if not secret or not Path(secret).exists():
                raise YouTubeError("YOUTUBE_CLIENT_SECRET 환경변수가 client_secret.json 경로를 가리켜야 합니다.")
            flow = InstalledAppFlow.from_client_secrets_file(secret, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload_short(
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = "private",   # 처음엔 private 로 올리고 대시보드에서 확인 후 공개 권장
    category_id: str = "22",           # People & Blogs. 교육=27, 과학기술=28
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> str:
    """업로드 후 video_id 반환."""
    from googleapiclient.http import MediaFileUpload

    if "#shorts" not in (title + description).lower():
        description = f"{description}\n\n#Shorts"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    request = _service(token_path).videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]
