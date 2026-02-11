import json
import os
import time
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv

# .env 파일에서 설정값 로드
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 클라이언트 초기화
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

def upload_knowledge():
    json_path = 'homefix_data.json'
    
    if not os.path.exists(json_path):
        print(f"❌ '{json_path}' 파일이 없습니다. 먼저 make_json.py를 실행해주세요.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"🚀 총 {len(data)}개의 데이터를 업로드하기 시작합니다...")

    # ✅ 배치 사이즈 설정 (한 번에 50개씩 묶어서 처리)
    batch_size = 50
    
    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]
        batch_to_insert = []
        
        # 1. 이번 묶음의 텍스트들만 추출
        texts = [item['content'].replace("\n", " ") for item in batch]
        
        try:
            # 2. 텍스트 임베딩 '한 번에' 생성 (API 요청 횟수 대폭 감소)
            res = client.embeddings.create(
                input=texts,
                model="text-embedding-3-small"
            )
            embeddings = [record.embedding for record in res.data]

            # 3. 데이터 준비
            for j, item in enumerate(batch):
                batch_to_insert.append({
                    "content": item['content'],
                    "metadata": item['metadata'],
                    "embedding": embeddings[j]
                })

            # 4. Supabase에 '한 번에' 저장 (Batch Insert)
            supabase.table("homefix_knowledge").insert(batch_to_insert).execute()
            
            print(f"✅ {min(i + batch_size, len(data))} / {len(data)} 개 업로드 완료...")
            
            # 레이트 리밋 방지를 위한 짧은 휴식 (엔지니어링 매너)
            time.sleep(0.1)

        except Exception as e:
            print(f"❌ {i}번째 묶음 업로드 중 에러 발생: {e}")
            # 에러 발생 시 0.5초 쉬고 다음 묶음 진행
            time.sleep(0.5)
            continue

    print("-" * 30)
    print(f"🎉 모든 {len(data)}개의 데이터 업로드가 완료되었습니다!")

if __name__ == "__main__":
    upload_knowledge()