import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv

# 1. 환경 변수 로드
load_dotenv()

# 2. DB 연결 정보
DB_HOST = os.getenv("TIDB_HOST")
DB_USER = os.getenv("TIDB_USER")
DB_PASS = os.getenv("TIDB_PASSWORD")
DB_NAME = os.getenv("TIDB_DB_NAME")
DB_PORT = "4000"

# 3. DB 연결 및 SSL 보안 설정
DB_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(
    DB_URL, 
    connect_args={"ssl": {"ca": None}}  # TiDB Cloud 필수 보안 설정
)

def upload_all_csvs():
    # [수정 포인트] 파일이 있는 '현재 폴더'를 직접 작업 경로로 설정합니다.
    target_dir = os.path.dirname(os.path.abspath(__file__))
    
    print(f"📂 탐색할 실제 폴더: {target_dir}")
    
    if not os.path.exists(target_dir):
        print(f"❌ 폴더를 찾을 수 없습니다: {target_dir}")
        return

    # 폴더 내 모든 CSV 파일 목록 가져오기
    csv_files = [f for f in os.listdir(target_dir) if f.lower().endswith('.csv')]
    print(f"🔍 발견된 CSV 파일들 ({len(csv_files)}개): {csv_files}")

    if not csv_files:
        print("❌ 에러: 폴더 내에 CSV 파일이 하나도 없습니다!")
        return

    for file in csv_files:
        print(f"📊 '{file}' 분석 및 TiDB 전송 중...")
        try:
            file_path = os.path.join(target_dir, file)
            
            # 한글 깨짐 방지를 위한 인코딩 시도 (cp949 -> utf-8-sig)
            try:
                df = pd.read_csv(file_path, encoding='cp949')
            except:
                df = pd.read_csv(file_path, encoding='utf-8-sig')
            
            # [꿀팁] 테이블 이름에 공백이나 특수문자가 있으면 SQL 에러가 나기 쉽습니다.
            # 파일명에서 확장자를 떼고, 공백을 언더바(_)로 바꿉니다.
            table_name = os.path.splitext(file)[0].replace(" ", "_").replace("-", "_")
            
            # TiDB에 저장 (if_exists='replace'는 기존에 있으면 덮어씌웁니다)
            df.to_sql(table_name, con=engine, if_exists='replace', index=False)
            print(f"✅ '{table_name}' 테이블 업로드 성공!")
            
        except Exception as e:
            print(f"❌ '{file}' 처리 중 에러 발생: {e}")

if __name__ == "__main__":
    upload_all_csvs()
    print("\n🚀 모든 CSV 데이터 전송 프로세스가 완료되었습니다!")