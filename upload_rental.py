import pandas as pd
import sqlite3
import os

def upload_rental_data():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # 기존 데이터가 있다면 중복 방지를 위해 삭제 (선택 사항)
    # cursor.execute("DELETE FROM resources WHERE category = '대여소'")

    folder = "homefix/" 
    files = [
        "과천도시공사_무료대여 생활공구_20250219.csv",
        "대구광역시 달서구_공구대여_20250514.csv",
        "대구광역시 서구_생활공구대여서비스 목록_20250811.csv",
        "생활공구 대여소 운영 현황_20191217..csv",
        "서울특별시 동대문구_공구대여소_20240813.csv",
        "서울특별시 성북구_공구대여소 현황_20250312.csv",
        "서울특별시 양천구_공구대여_20241017.csv",
        "인천광역시 남동구_행정복지센터 생활공구 대여소_20250101.csv",
        "인천광역시 미추홀구_공구대여소현황_20241029.csv"
    ]

    print("🚀 공구 대여 데이터 통합 업로드 시작...")

    for file_name in files:
        # 경로 결합
        file = os.path.join(folder, file_name)
        
        if not os.path.exists(file):
            print(f"⚠️ 파일을 찾을 수 없음: {file}")
            continue

        print(f"📂 처리 중: {file}")
        try:
            # 인코딩 처리 (공공데이터는 보통 cp949)
            df = pd.read_csv(file, encoding='cp949')
        except:
            df = pd.read_csv(file, encoding='utf-8')

        # --- 파일별 맞춤 로직 ---
        
        # 1. 과천
        if "과천" in file:
            for _, row in df.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, link, description) VALUES (?,?,?,?,?,?)",
                    ('대여소', row['공구대여소'], row['도로명주소'], row['문의'], row['홈페이지'], f"공구: {row['공구종류']} / 대여조건: {row['대여조건']}"))

        # 2. 대구 달서구/서구 (동별로 공구가 나뉘어 있어 그룹화 필요)
        elif "대구" in file:
            dong_col = '행정동' if '행정동' in df.columns else '동명'
            tool_col = '도구명' if '도구명' in df.columns else '품명'
            grouped = df.groupby(dong_col).agg({tool_col: lambda x: ', '.join(x.unique())}).reset_index()
            for _, row in grouped.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)",
                    ('대여소', f"대구 {row[dong_col]} 대여소", f"대구광역시 {row[dong_col]} 주민센터", "현장 문의", f"보유 공구: {row[tool_col]}"))

        # 3. 안산 (생활공구 대여소 운영 현황)
        elif "운영 현황" in file:
            for _, row in df.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)",
                    ('대여소', row['사업장'], row['위치'], row['내선번호'], f"운영시간: {row['운영시간']}"))

        # 4. 동대문구
        elif "동대문구" in file:
            for _, row in df.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)",
                    ('대여소', row['공구대여소명'], row['도로명 주소'], row['연락처'], f"비치공구: {row['비치공구']} / 시간: {row['운영시간']}"))

        # 5. 성북구
        elif "성북구" in file:
            for _, row in df.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)",
                    ('대여소', row['공구대여소'], row['소재지도로명주소'], row['전화번호'], f"공구: {row['비치공구류']} / 대여료: {row['대여료']}"))

        # 6. 양천구
        elif "양천구" in file:
            for _, row in df.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)",
                    ('대여소', row['명칭'], row['도로명'], row['전화번호'], f"공구: {row['공구보유현황']} / 대여한도: {row['대여한도']}"))

        # 7. 인천 (남동구, 미추홀구)
        elif "인천" in file:
            for _, row in df.iterrows():
                cursor.execute("INSERT INTO resources (category, name, location, contact, description) VALUES (?,?,?,?,?)",
                    ('대여소', row['기관명'], row['도로명주소'], row['전화번호'], "생활공구 대여 서비스 제공"))

    conn.commit()
    conn.close()
    print("✨ 모든 공구 대여 데이터가 resources 테이블에 저장되었습니다!")

if __name__ == "__main__":
    upload_rental_data()