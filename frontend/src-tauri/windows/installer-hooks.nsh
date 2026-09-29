; K5/PREINSTALL：覆盖安装前杀干净进程，避免文件占用导致 Error opening file for writing
!macro NSIS_HOOK_PREINSTALL
  nsExec::ExecToLog 'taskkill /F /IM scripthub-server.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM scripthub.exe /T'
  Sleep 800
!macroend

; K5/K6：卸载删文件前先杀进程（来不及走壳内 kill_tree），再问一次是否删数据。
; 删的是 $APPDATA/$LOCALAPPDATA\com.scripthub.app——与卸载完成页
; 「Delete application data」勾选删的是同一目录集合，两者等效、重复勾选幂等无害。
!macro NSIS_HOOK_PREUNINSTALL
  nsExec::ExecToLog 'taskkill /F /IM scripthub-server.exe /T'
  nsExec::ExecToLog 'taskkill /F /IM scripthub.exe /T'
  Sleep 800
  MessageBox MB_YESNO "是否同时删除用户数据（脚本配置、运行历史、设备配置）？$\n选「否」将保留数据，供日后重装使用。$\n（安装程序卸载页的删除数据选项与此等效）" IDYES scripthub_del_data IDNO scripthub_done
  scripthub_del_data:
    RMDir /r "$APPDATA\com.scripthub.app"
    RMDir /r "$LOCALAPPDATA\com.scripthub.app"
  scripthub_done:
!macroend

; 删数据已前移到 PREUNINSTALL（K6 对齐口径），此钩子不再需要内容
!macro NSIS_HOOK_POSTUNINSTALL
!macroend
