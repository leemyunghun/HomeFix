import os
import json
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv
import PyPDF2

load_dotenv()

# DB 연결 설정
DB_URL = f"mysql+pymysql://{os.getenv('TIDB_USER')}:{os.getenv('TIDB_PASSWORD')}@{os.getenv('TIDB_HOST')}:4000/{os.getenv('TIDB_DB_NAME')}"
engine = create_engine(DB_URL, connect_args={"ssl": {"ca": None}})

def total_migration():
    # 1️⃣ [DB 보수] 테이블 구조 업데이트 (용량 확장 및 컬럼 추가)
    with engine.connect() as conn:
        print("🛠️ DB 구조 보수 중...")
        
        # [manual_data] 컬럼 확장
        conn.execute(text("ALTER TABLE manual_data MODIFY COLUMN content LONGTEXT"))
        
        # [users] role 컬럼 추가
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'"))
            print("✅ 'users' 테이블에 'role' 컬럼 추가 완료!")
        except Exception as e:
            if "Duplicate column name" in str(e): print("ℹ️ 'users.role' 컬럼이 이미 존재합니다.")

        # ✨ [support] 테이블 보수 (에러 해결 핵심 부분)
        print("🎧 support 테이블 구조 업데이트 중...")
        try:
            # answer(답변) 컬럼 추가
            conn.execute(text("ALTER TABLE support ADD COLUMN answer TEXT"))
            print("✅ 'support' 테이블에 'answer' 컬럼 추가 완료!")
        except Exception as e:
            if "Duplicate column name" in str(e): print("ℹ️ 'support.answer' 컬럼이 이미 존재합니다.")

        try:
            # status(상태) 컬럼 추가 (기본값 '대기중')
            conn.execute(text("ALTER TABLE support ADD COLUMN status VARCHAR(20) DEFAULT '대기중'"))
            print("✅ 'support' 테이블에 'status' 컬럼 추가 완료!")
        except Exception as e:
            if "Duplicate column name" in str(e): print("ℹ️ 'support.status' 컬럼이 이미 존재합니다.")

        # 관리자 권한 부여
        conn.execute(text("UPDATE users SET role = 'admin' WHERE userid = 'jhdbwlsgus'"))
        
        conn.commit()
        print("✅ DB 기초 보수가 완료되었습니다.")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(current_dir, 'homefix')
    
    # 2️⃣ [PDF 업로드]
    pdf_files = [f for f in os.listdir(target_dir) if f.lower().endswith('.pdf')]
    for file_name in pdf_files:
        file_path = os.path.join(target_dir, file_name)
        try:
            text_content = ""
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted: text_content += extracted + "\n"
            
            if text_content.strip():
                df_pdf = pd.DataFrame([{"file_name": file_name, "content": text_content}])
                df_pdf.to_sql("manual_data", con=engine, if_exists='append', index=False)
                print(f"✅ PDF 성공: {file_name}")
        except Exception as e:
            print(f"❌ PDF 에러 ({file_name}): {e}")

    # 3️⃣ [업체 및 대여소 CSV 자동 탐색]
    csv_files = [f for f in os.listdir(target_dir) if f.lower().endswith('.csv')]
    mapping = {
        'name': ['업체명', '공구대여소', '공구대여소명', '명칭', '기관명'],
        'location': ['업체주소', '도로명주소', '도로명 주소', '소재지도로명주소', '도로명'],
        'contact': ['업체연락처', '문의', '연락처', '전화번호'],
        'desc': ['주요시공분야', '공구종류', '비치공구', '비치공구류', '공구보유현황'],
        'lat': ['위도'],
        'lon': ['경도']
    }

    for csv_name in csv_files:
        path = os.path.join(target_dir, csv_name)
        try:
            df_raw = None
            for enc in ['cp949', 'utf-8-sig', 'utf-8']:
                try: df_raw = pd.read_csv(path, encoding=enc); break
                except: continue
            
            if df_raw is None: continue

            category = 'expert' if '업체' in csv_name else '대여소'
            final_df = pd.DataFrame()
            final_df['category'] = [category] * len(df_raw)
            
            for key, col_list in mapping.items():
                target_col = next((c for c in col_list if c in df_raw.columns), None)
                if target_col:
                    final_df[key if key != 'desc' else 'description'] = df_raw[target_col]
                else:
                    final_df[key if key != 'desc' else 'description'] = None

            final_df.to_sql("resources", con=engine, if_exists='append', index=False)
            print(f"✅ CSV 성공: {csv_name} ({len(final_df)}건)")
        except Exception as e:
            print(f"❌ CSV 에러 ({csv_name}): {e}")

    # 4️⃣ [JSON 데이터 복구]
    json_path = next((p for p in [os.path.join(current_dir, 'homefix_data_final.json'), 
                                os.path.join(target_dir, 'homefix_data_final.json')] if os.path.exists(p)), None)
    
    if json_path:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            rental_list = []
            for item in data:
                meta = item.get('metadata', {})
                if '대여' in meta.get('source', '') or '대여' in meta.get('name', ''):
                    rental_list.append({
                        'category': '대여소',
                        'name': meta.get('name'),
                        'location': meta.get('location'),
                        'contact': meta.get('contact'),
                        'description': item.get('content'),
                        'lat': meta.get('lat'),
                        'lon': meta.get('lon')
                    })
            
            if rental_list:
                pd.DataFrame(rental_list).to_sql("resources", con=engine, if_exists='append', index=False)
                print(f"✅ JSON 대여소 주입 성공: {len(rental_list)}건")
        except Exception as e:
            print(f"❌ JSON 에러: {e}")

if __name__ == "__main__":
    total_migration()
    print("\n🚀 모든 데이터 보수 및 전송이 완료되었습니다!")