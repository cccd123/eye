@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
REM ============================================================
REM  probe_glasses.bat - 读取眼镜主板信息（只读，不会改动板子上任何东西）
REM
REM  用之前：
REM   1. 电池按标注图 ⑪ 接好（红线 V+，黑线 BAT-）
REM   2. 用数据线把标注图 ④「调试USB/烧录」那根线连到电脑
REM   3. 双击本文件。结果保存在同目录 probe_result.txt，发给 Claude
REM ============================================================
set "OUT=%~dp0probe_result.txt"
set "ADB=adb"

where adb >nul 2>nul && goto have_adb
if exist "%~dp0platform-tools\adb.exe" goto local_adb
echo 第一次运行：正在下载 adb 工具（约 7MB）...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile '%~dp0pt.zip'; Expand-Archive '%~dp0pt.zip' '%~dp0' -Force; Remove-Item '%~dp0pt.zip'"
if not exist "%~dp0platform-tools\adb.exe" goto no_download
:local_adb
set "ADB=%~dp0platform-tools\adb.exe"
:have_adb

echo ===== 第1步：USB 设备列表（不管 adb 通不通都有用） ===== > "%OUT%"
powershell -NoProfile -Command "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB*' } | Format-Table -AutoSize Status,Class,FriendlyName,InstanceId | Out-String -Width 300" >> "%OUT%" 2>&1

echo.
echo 正在连接眼镜...
"%ADB%" start-server >nul 2>&1
"%ADB%" devices >> "%OUT%" 2>&1
"%ADB%" get-state >nul 2>&1
if errorlevel 1 goto no_device

echo 已连接！正在读取信息（约 10 秒）...
"%ADB%" shell "echo ===SYSTEM===; uname -a; cat /proc/cpuinfo; cat /etc/openwrt_release /etc/os-release 2>/dev/null; cat /proc/cmdline; free; df -h" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===CAMERA===; ls -l /dev/video* /dev/media* 2>&1; dmesg | grep -iE 'vin|csi|sensor|gc[0-9]|sc[0-9]|ov[0-9]|camera' | tail -40" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===AUDIO===; ls -l /dev/snd 2>&1; cat /proc/asound/cards 2>&1; cat /proc/asound/pcm 2>&1" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===INPUT===; cat /proc/bus/input/devices 2>&1; ls -l /dev/input 2>&1" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===POWER_LED===; ls /sys/class/power_supply /sys/class/leds 2>&1; cat /sys/class/power_supply/*/uevent 2>&1" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===NETWORK===; ifconfig -a 2>&1 || ip addr; ls /etc/wifi 2>&1" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===TOOLS===; for t in sh busybox curl wget od dd hexdump getevent arecord aplay tinycap tinyplay tinymix amixer camerademo yavta fswebcam ffmpeg gst-launch-1.0 wpa_cli wpa_supplicant wifi_connect_ap_test wifid udhcpc python3 lua; do printf '%%s: ' $t; which $t 2>/dev/null || echo -; done" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===BIN===; ls /usr/bin /usr/sbin /bin /sbin 2>&1" >> "%OUT%" 2>&1
"%ADB%" shell "echo ===PROCS===; ps 2>&1; echo ===INIT===; ls /etc/init.d 2>&1" >> "%OUT%" 2>&1
echo.
echo 完成！结果在：%OUT%
echo 把这个文件发给 Claude。
goto end

:no_device
echo.
echo [!] 没有检测到眼镜。请依次检查：
echo     1. 电池接好了吗？红线 V+，黑线 BAT-（标注图 11）
echo     2. 数据线插的是「调试USB/烧录」那根线吗？（标注图 4）
echo     3. 换一根数据线试试，很多线只能充电不能传数据
echo     4. 打开「设备管理器」，插拔一次数据线，看多出了什么设备
echo.
echo 不管怎样，%OUT% 里已经记下了电脑看到的 USB 设备，也请发给 Claude。
goto end

:no_download
echo.
echo [!] adb 下载失败。请手动下载：
echo     https://dl.google.com/android/repository/platform-tools-latest-windows.zip
echo     解压后把 platform-tools 文件夹放到本文件旁边，再双击本文件。

:end
pause
