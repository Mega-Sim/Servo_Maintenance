# Git 저장소 초기화

이 폴더에서 다음 명령을 순서대로 실행합니다.

```bash
git init
git branch -M main
git add .
git status
git commit -m "초기 구현: Linux Web 및 EC-Master FoE 펌웨어 업데이트"
```

사용자 이름이나 이메일 오류가 발생할 때만 회사 정책에 맞는 값을 설정합니다.

```bash
git config user.name "이름"
git config user.email "회사메일"
```

실제 `.bin` 파일은 저장소에 커밋하지 않도록 `.gitignore`에 포함되어 있습니다.
