@echo off
rem Zapusk Konspekt s podrobnym zhurnalom.
rem
rem Kommentarii latinicej namerenno: cmd chitaet .bat v kodirovke cp866, a
rem fajly proekta hranjatsja v utf-8. Russkij tekst zdes prevrashaetsja v
rem musor i lomaet zapusk - eto uzhe slomalo fajl odin raz.
rem
rem Nuzhen, kogda programma molchit i neponjatno pochemu. Naprimer: v
rem sistemnom zvuke igraet lekcija, a vopros ne pojavljaetsja. S etim
rem zapuskom v zhurnal pishetsja razbor kazhdogo okna zvuka: dolja zvuka,
rem ritm, tembr - i srazu vidno, chto imenno ne soshlos.
rem
rem Zhurnal: %APPDATA%\Konspekt (razrabotka)\konspekt.log
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set KONSPEKT_LOG=debug
if "%KONSPEKT_PROFILE%"=="" set KONSPEKT_PROFILE=razrabotka
start "" /b uv run pythonw -m app
