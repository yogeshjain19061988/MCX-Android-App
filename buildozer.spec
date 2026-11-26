[app]
title = MCX Trade Pro
package.name = mcxtradepro
package.domain = org.jainy

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

requirements = python3==3.14.2,hostpython3==3.14.2,kivy,pyjnius,plyer,requests,certifi,urllib3,python-dateutil

orientation = portrait
android.archs = arm64-v8a
android.minapi = 26
android.api = 36
android.ndk = 28c
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE
p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1
