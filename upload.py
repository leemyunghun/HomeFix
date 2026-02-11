import json
import os
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv

# .env 파일에서 설정값 로드
load_dotenv()

# 환경 변수 확인 (직접 입력하지 않아도 .env 파일이 있으면 자동으로 읽어옵니다)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 클라이언트 초기화
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

def upload_knowledge():
    # 1. 파일 경로 확인 (make_json.py로 만든 파일이 있는 위치)
    json_path = 'homefix_data.json'
    
    if not os.path.exists(json_path):
        print(f"❌ '{json_path}' 파일이 없습니다. 먼저 make_json.py를 실행해주세요.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"🚀 총 {len(data)}개의 데이터를 업로드하기 시작합니다...")

    for i, item in enumerate(data):
        try:
            # 2. 텍스트 임베딩 생성 (AI용 숫자로 변환)
            res = client.embeddings.create(
                input=item['content'].replace("\n", " "),
                model="text-embedding-3-small"
            )
            embedding = res.data[0].embedding

            # 3. Supabase 테이블에 저장
            # 주의: Supabase에 'homefix_knowledge' 테이블이 미리 생성되어 있어야 합니다.
            supabase.table("homefix_knowledge").insert({
                "content": item['content'],
                "metadata": item['metadata'],
                "embedding": embedding
            }).execute()

            if (i + 1) % 10 == 0:
                print(f"✅ {i + 1}개 업로드 중...")

        except Exception as e:
            # 에러 발생 시 상세 내용 출력
            print(f"❌ {i}번째 데이터 업로드 에러: {e}")
            continue

    print("-" * 30)
    print("🎉 모든 데이터 업로드가 완료되었습니다!")

if __name__ == "__main__":
    upload_knowledge()