import sqlite3
import pandas as pd
import os

def upload_contractors():
    # 1. DB 연결
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # ✅ 파일 경로 수정: 하위 폴더인 'homefix'를 포함합니다.
    file_path = os.path.join('homefix', '서울시 집수리 시공업체 정보.csv')
    
    if not os.path.exists(file_path):
        print(f"❌ 파일을 찾을 수 없습니다: {file_path}")
        print("현재 폴더 내 'homefix' 폴더 안에 해당 CSV 파일이 있는지 확인해주세요.")
        return

    try:
        # 인코딩 대응 (utf-8-sig -> cp949)
        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig')
        except:
            df = pd.read_csv(file_path, encoding='cp949')
    except Exception as e:
        print(f"❌ 파일 읽기 오류: {e}")
        return

    print(f"📊 총 {len(df)}개의 업체 데이터를 로드했습니다.")

    # 3. 데이터 삽입
    count = 0
    for _, row in df.iterrows():
        try:
            category = 'expert'
            name = str(row['업체명'])
            location = str(row['업체주소'])
            contact = str(row['업체연락처']) if pd.notna(row['업체연락처']) else "정보없음"
            
            # 상세 내용 구성
            description = f"사업종목: {row['사업종목']} | 시공분야: {row['주요시공분야']}"
            link = "" 

            cursor.execute('''
                INSERT INTO resources (category, name, location, contact, link, description)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (category, name, location, contact, link, description))
            count += 1
        except Exception as e:
            continue

    conn.commit()
    conn.close()
    print(f"✅ 총 {count}개의 업체 정보가 DB(resources 테이블)에 저장되었습니다.")

if __name__ == "__main__":
    upload_contractors()