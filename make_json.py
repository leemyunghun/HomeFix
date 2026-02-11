import json
import os
from langchain_community.document_loaders import PyPDFLoader

def make_homefix_json():
    final_data = []

    # 폴더 이름을 homefix/ 로 수정했습니다.
    folder = "homefix/"
    
    pdf_files = [
        (folder + "★공동주택 보수공사 길라잡이(책자발간본)★.pdf", "보수공사 길라잡이"),
        (folder + "경기도 공동주택관리매뉴얼 현황.pdf", "공동주택관리 매뉴얼")
    ]

    for filename, source_name in pdf_files:
        if os.path.exists(filename):
            print(f"📖 {filename} 읽는 중...")
            try:
                loader = PyPDFLoader(filename)
                pages = loader.load()
                for page in pages:
                    if len(page.page_content.strip()) > 50:
                        final_data.append({
                            "content": page.page_content.strip(),
                            "metadata": {
                                "source": source_name,
                                "page": page.metadata.get("page", 0) + 1
                            }
                        })
            except Exception as e:
                print(f"에러 발생: {e}")
        else:
            print(f"❌ 파일을 찾을 수 없습니다: {filename}")
            print(f"현재 경로에 '{folder}' 폴더가 있고 그 안에 PDF가 있는지 확인하세요.")

    # 결과 저장
    with open('homefix_data.json', 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ 완료! 총 {len(final_data)}개의 데이터가 생성되었습니다.")

if __name__ == "__main__":
    make_homefix_json()