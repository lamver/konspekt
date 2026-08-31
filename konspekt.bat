@echo off
rem Zapusk Konspekt iz ishodnikov dvojnym schelchkom.
rem
rem Kommentarii latinicej namerenno: cmd chitaet .bat v kodirovke cp866, a
rem fajly proekta hranjatsja v utf-8. Russkij tekst zdes prevrashaetsja v
rem musor i lomaet zapusk - eto uzhe slomalo fajl odin raz.
rem
rem Profil "razrabotka" razvodit etu kopiju s ustanovlennoj: svoja baza,
rem svoi zapisi, svoi vesa i svoj zamok edinstvennogo ekzempljara. Bez nego
rem zapusk rjadom s ustanovlennoj programmoj ne podnimal vtoruju kopiju
rem vovse, a dannye obe kopii pisali v odnu papku.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if "%KONSPEKT_PROFILE%"=="" set KONSPEKT_PROFILE=razrabotka
start "" /b uv run pythonw -m app
