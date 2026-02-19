import os
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
import PyPDF2

# 1. 환경 변수 로드
load_dotenv()

# 2. DB 연결 정보 (test 데이터베이스 사용)
DB_HOST = os.getenv("TIDB_HOST")
DB_USER = os.getenv("TIDB_USER")
DB_PASS = os.getenv("TIDB_PASSWORD")
DB_NAME = os.getenv("TIDB_DB_NAME")
DB_PORT = "4000"

# 3. 연결 주소 및 SSL 보안 설정 적용 (에러 해결 핵심)
DB_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# TiDB Cloud는 보안 연결이 필수이므로 connect_args에 ssl 설정을 넣습니다.
engine = create_engine(
    DB_URL, 
    connect_args={"ssl": {"ca": None}} # SSL 보안 연결 활성화
)

def final_manual_upload():
    # 현재 실행 경로에서 한 단계 아래인 'homefix' 폴더를 지정합니다.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(current_dir, 'homefix') 
    
    print(f"📂 탐색할 실제 폴더: {target_dir}")
    
    if not os.path.exists(target_dir):
        print(f"❌ 에러: {target_dir} 폴더를 찾을 수 없습니다. 폴더명을 확인하세요!")
        return

    # PDF 파일 목록 가져오기
    pdf_files = [f for f in os.listdir(target_dir) if f.lower().endswith('.pdf')]
    print(f"🔍 발견된 매뉴얼 파일들: {pdf_files}")
    
    if not pdf_files:
        print("❌ 에러: 해당 폴더 안에 PDF 파일이 없습니다.")
        return

    for file_name in pdf_files:
        file_path = os.path.join(target_dir, file_name)
        print(f"📄 '{file_name}' 분석 및 전송 중...")
        try:
            text = ""
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
            
            if text.strip():
                # 데이터프레임 생성
                df = pd.DataFrame([{"file_name": file_name, "content": text}])
                
                # manual_data 테이블에 추가 (없으면 생성, 있으면 데이터 추가)
                df.to_sql("manual_data", con=engine, if_exists='append', index=False)
                print(f"✅ {file_name} -> TiDB 업로드 성공!")
            else:
                print(f"⚠️ {file_name}에서 추출된 텍스트가 없습니다.")
                
        except Exception as e:
            print(f"❌ {file_name} 처리 중 에러 발생: {e}")

if __name__ == "__main__":
    final_manual_upload()
    print("\n🚀 모든 수리 지식 데이터 전송이 완료되었습니다!")