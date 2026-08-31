@echo off
rem Proverka sobrannoj programmy v otdelnom profile.
rem
rem Kommentarii latinicej: cmd chitaet .bat v cp866, a fajly proekta v
rem utf-8, i russkij tekst zdes lomaet zapusk.
rem
rem Profil objazatelen: bez nego proverka polezet v dannye polzovatelja i
rem upretsja v zamok uzhe zapushennoj programmy. Proshlyj variant etoj
rem proverki stavil ustanovshik vo vremennuju papku i pereuchival
rem nastojashuju ustanovku obnovljatsja tuda zhe.
set KONSPEKT_PROFILE=proverka
start "" "%~dp0..\dist\Konspekt\Konspekt.exe"
