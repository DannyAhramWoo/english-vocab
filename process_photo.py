#!/usr/bin/env python3
"""
영어 단어 사진 → data.js 자동 추출
사진을 photos/학원/ 또는 photos/학교/ 에 업로드하면 자동 실행됩니다.
"""

import anthropic
import base64
import json
import re
import sys
from pathlib import Path
from datetime import date

PROCESSED_FILE = Path('photos/processed.txt')

def load_processed():
    if PROCESSED_FILE.exists():
        return set(PROCESSED_FILE.read_text(encoding='utf-8').splitlines())
    return set()

def mark_processed(filename):
    existing = load_processed()
    existing.add(filename)
    PROCESSED_FILE.write_text('\n'.join(sorted(existing)) + '\n', encoding='utf-8')

def safe_parse_json(raw):
    raw = re.sub(r'^```json\s*', '', raw.strip())
    raw = re.sub(r'\s*```$', '', raw.strip())
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Character-by-character scan to escape control chars inside strings
    result = []
    in_string = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == '\\' and in_string:
            result.append(c); i += 1
            if i < len(raw): result.append(raw[i])
            i += 1; continue
        if c == '"':
            in_string = not in_string
            result.append(c); i += 1; continue
        if in_string and c in '\n\r\t':
            escape_map = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
            result.append(escape_map[c]); i += 1; continue
        result.append(c); i += 1
    fixed = ''.join(result)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError as e:
        pos = e.pos
        snippet = repr(fixed[max(0, pos-80):pos+80])
        print(f"  → 파싱 실패 상세: char {pos} 주변: {snippet}")
        raise

def encode_image(path):
    with open(path, 'rb') as f:
        return base64.standard_b64encode(f.read()).decode('utf-8')

def get_media_type(path):
    suffix = path.suffix.lower()
    return {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp'}.get(suffix, 'image/jpeg')

PROMPT = """
이 사진은 싱가포르 초등학생의 영어 단어 학습 자료입니다.
아래 규칙에 따라 단어를 추출하고 JSON으로 반환하세요.

## 사진 유형별 처리 규칙

### 📘 Spelling List (번호 붙은 문장, 단어에 밑줄)
- 밑줄 친 단어(vocabulary word)만 추출
- 해당 단어가 포함된 전체 문장을 sentence로 저장
- 그룹명: "Spelling List"

### 📋 Word List (단어 목록표)
- 열에 있는 영어 단어만 추출
- 그룹명: 표 제목이 있으면 사용, 없으면 "Vocabulary"

### 📝 Definition/Matching 시트
- 단어와 뜻이 매칭되는 형식 → 단어와 definition 함께 추출
- 그룹명: "Definitions"

## 공통 규칙
- 손글씨(연필/펜 메모)는 무시, 인쇄된 텍스트만 추출
- lesson: 사진에 보이는 제목/단원 정보 (예: "T2.1 Unit 4: Predators and Prey")
- 각 단어에 대해 AI가 한국어 뜻(korean)과 한국어 설명(definition)을 생성
- pronunciation: 영어 발음을 대문자 음절로 표기 (예: "PRED-uh-ter")
- sentence가 없으면 빈 문자열 ""로 설정
- JSON 문자열 안에 큰따옴표 필요시 \" 로 이스케이프

## 출력 형식 (JSON만 반환, 다른 텍스트 없음)
{
  "lesson": "단원 제목",
  "source_hint": "학교 또는 학원",
  "groups": [
    {
      "name": "그룹명",
      "words": [
        {
          "word": "영어단어",
          "pronunciation": "발음표기",
          "korean": "한국어뜻",
          "definition": "한국어로 간단한 설명",
          "sentence": "예문 문장 (없으면 빈 문자열)"
        }
      ]
    }
  ]
}
"""

def extract_words(client, photo_path):
    print(f"  처리 중: {photo_path.name}")
    img_data = encode_image(photo_path)
    media_type = get_media_type(photo_path)

    msg = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=4000,
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': img_data}},
                {'type': 'text', 'text': PROMPT}
            ]
        }]
    )
    raw = msg.content[0].text
    print(f"  → AI 응답 ({len(raw)}자)")
    return safe_parse_json(raw)

def update_data_js(new_session, data_js_path):
    content = Path(data_js_path).read_text(encoding='utf-8')
    session_str = json.dumps(new_session, ensure_ascii=False, indent=2)
    session_indented = '\n'.join('  ' + line for line in session_str.split('\n'))
    idx = content.rfind('];')
    if idx == -1:
        print("ERROR: data.js에서 ]; 를 찾을 수 없습니다.")
        return False
    new_content = content[:idx].rstrip() + ',\n' + session_indented + '\n];\n'
    Path(data_js_path).write_text(new_content, encoding='utf-8')
    return True

def session_exists(session_id, data_js_path):
    content = Path(data_js_path).read_text(encoding='utf-8')
    return session_id in content

def main():
    api_key = None
    import os
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY 환경변수가 없습니다.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    data_js = Path('data.js')
    photos_dir = Path('photos')

    if not data_js.exists():
        print("ERROR: data.js 파일이 없습니다.")
        sys.exit(1)

    processed = load_processed()
    photo_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.HEIC'}

    photos = []
    for source_dir in ['학원', '학교']:
        d = photos_dir / source_dir
        if d.exists():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in photo_extensions or p.suffix in photo_extensions:
                    photos.append(p)

    new_photos = [p for p in photos if p.name not in processed]

    if not new_photos:
        print("새로운 사진이 없습니다. 처리 종료.")
        return

    print(f"새 사진 {len(new_photos)}개 처리 시작...")
    today = date.today().strftime('%Y-%m-%d')

    for photo in new_photos:
        print(f"\n[{photo.name}]")
        source = '학원'
        for part in photo.parts:
            if part == '학교':
                source = '학교'
                break
            elif part == '학원':
                source = '학원'
                break

        try:
            extracted = extract_words(client, photo)
            lesson = extracted.get('lesson', photo.stem)
            groups = extracted.get('groups', [])

            if not groups or not any(g.get('words') for g in groups):
                print(f"  → 단어 없음, 건너뜀")
                mark_processed(photo.name)
                continue

            # source_hint가 있으면 반영
            source_hint = extracted.get('source_hint', '')
            if '학교' in source_hint:
                source = '학교'
            elif '학원' in source_hint:
                source = '학원'

            lesson_slug = re.sub(r'[^a-zA-Z0-9]', '', lesson) or photo.stem
            prefix = '학교' if source == '학교' else '학원'
            date_str = today.replace('-', '')
            session_id = f"{prefix.lower()}_{date_str}_{lesson_slug[:20]}"

            if session_exists(session_id, data_js):
                print(f"  → 세션 {session_id} 이미 존재, 건너뜀")
                mark_processed(photo.name)
                continue

            total_words = sum(len(g.get('words', [])) for g in groups)
            print(f"  → 레슨: {lesson}, 단어 {total_words}개")

            session = {
                'id': session_id,
                'source': source,
                'date': today,
                'lesson': lesson,
                'groups': groups
            }

            if update_data_js(session, data_js):
                mark_processed(photo.name)
                print(f"  → data.js 업데이트 완료")
            else:
                print(f"  → data.js 업데이트 실패")

        except Exception as e:
            print(f"  → 오류: {e}")
            import traceback
            traceback.print_exc()

    print("\n처리 완료!")

if __name__ == '__main__':
    main()
