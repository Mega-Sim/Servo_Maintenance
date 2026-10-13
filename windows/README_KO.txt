Windows IPC에서는 별도 프로그램을 실행하지 않습니다.

1. Linux 보드의 servo-maintenance-bridge와 servo-maintenance-web 서비스가 실행 중인지 확인합니다.
2. Servo_Maintenance.url을 더블 클릭합니다.
3. Internet Explorer에서 http://192.168.0.2:18765/ 화면이 열립니다.

화면이 열리지 않으면 Windows 명령 프롬프트에서 다음을 확인하십시오.
ping 192.168.0.2

Windows 역할은 화면 표시, 버튼 명령 전송, 파일 전송, 결과 표시뿐입니다.
실제 파라미터 처리와 펌웨어 업데이트는 Linux에서 수행됩니다.
