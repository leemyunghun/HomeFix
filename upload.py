import json
import os
import time
from openai import OpenAI
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# 환경 변수 확인
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)

def upload_knowledge():
    # ✅ 파일명을 최종 통합본인 'homefix_data_final.json'으로 변경
    json_path = 'homefix_data_final.json'
    
    if not os.path.exists(json_path):
        print(f"❌ '{json_path}' 파일이 없습니다. 경로를 확인해주세요.")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"🚀 총 {len(data)}개의 데이터를 업로드합니다...")

    # 배치 사이즈 (API 부하를 줄이기 위해 50개씩 묶음)
    batch_size = 50
    
    for i in range(0, len(data), batch_size):
        batch = data[i:i + batch_size]
        batch_to_insert = []
        
        # 이번 묶음의 텍스트만 추출
        texts = [item['content'].replace("\n", " ") for item in batch]
        
        try:
            # 1. 텍스트 -> 숫자 벡터로 변환 (Embedding)
            res = client.embeddings.create(
                input=texts,
                model="text-embedding-3-small"
            )
            embeddings = [record.embedding for record in res.data]

            # 2. 업로드 데이터 구성
            for j, item in enumerate(batch):
                batch_to_insert.append({
                    "content": item['content'],
                    "metadata": item['metadata'],
                    "embedding": embeddings[j]
                })

            # 3. Supabase 저장 (테이블 이름 주의: app.py의 rpc와 일치해야 함)
            # 만약 테이블명이 다르면 아래 이름을 수정하세요.
            supabase.table("homefix_knowledge").insert(batch_to_insert).execute()
            
            print(f"✅ {min(i + batch_size, len(data))} / {len(data)} 개 완료...")
            
        except Exception as e:
            print(f"❌ {i}번째 묶음 오류: {e}")
            time.sleep(1) # 에러 시 잠시 대기
            continue

    print("\n🎉 모든 데이터가 AI의 뇌(Supabase)에 성공적으로 저장되었습니다!")

if __name__ == "__main__":
    upload_knowledge()