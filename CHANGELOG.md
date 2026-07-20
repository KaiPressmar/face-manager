# Änderungsprotokoll

Dieses Protokoll wird in Face Manager angezeigt und beschreibt ausschließlich
Änderungen, die Anwender in der veröffentlichten Anwendung wahrnehmen können.
Technische Entwicklungs-, Test- und Release-Details gehören in Commits und Pull
Requests, nicht in diese Versionshinweise.

## [Unreleased]

### Neu

### Verbessert

### Behoben

## [0.6.0] - 2026-07-20

### Neu

- Eine neue Prüfansicht bündelt unsichere Gesichter, mögliche Fehl-Erkennungen und Vorschläge für bereits bekannte Personen an einem Ort.
- Bilder und Umbenennungen lassen sich jetzt flexibel nach mehreren Personen, Ordnern und Sortierungen filtern.
- Die Oberfläche unterstützt helle, dunkle und automatisch vom Betriebssystem übernommene Darstellung.
- Datenbank-Sicherungen können direkt in den Einstellungen exportiert und wieder eingespielt werden.
- Nach einem Update zeigt Face Manager die wichtigsten Neuerungen beim ersten Start an; über die Versionsnummer bleiben sie später erreichbar.
- Face Manager prüft stündlich auf neue GitHub-Versionen und kann den passenden Windows-Installer nach einer Sicherheitsprüfung herunterladen und auf ausdrücklichen Wunsch starten.

### Verbessert

- In der Vollbildansicht lässt sich direkt durch alle Gesichter der geöffneten Gruppe navigieren; dabei werden die zugehörige Person oder Kategorie, die konkrete Gesichtsgruppe und ausschließlich der Rahmen des aktuellen Crops angezeigt, ohne dort versehentlich ganze Bilder entfernen zu können.
- Dateipfade lassen sich in Bild-, Vollbild- und Umbenennungsansichten eindeutig kopieren oder im Explorer beziehungsweise Dateimanager öffnen; bei mehreren Speicherorten bleibt jeder Pfad separat auswählbar.
- Navigation, Seitenüberschriften, Einstellungen und Dialoge sind einheitlich benannt und leicht verständlich formuliert. Einheitliche Boxen, Hervorhebungen und Schatten machen Inhalte und Handlungsebenen schneller erfassbar.
- Seiten und Einstellungsbereiche besitzen einheitliche, aktualisierungssichere Adressen. Dadurch bleiben die aktuelle Ansicht sowie Zurück- und Vorwärtsnavigation im Browser und in der installierten Anwendung zuverlässig erhalten.
- Die Gesichtserkennung gruppiert vorsichtiger, berücksichtigt bestätigte Personen und kann ihre Empfindlichkeit anhand vorhandener Zuordnungen abstimmen.
- Die Kopfleiste ist platzsparend nach Bildimport, Aktivitäten und Darstellung gegliedert; Versionsinformationen und verfügbare Updates sind dezent in den Markenbereich der Sidebar integriert. Einheitliche Größen und Darstellungen sorgen für ein ruhigeres Gesamtbild. Hintergrundaufgaben bleiben dezent sichtbar, ihre verständlichen Einzelheiten und kürzlich abgeschlossene Arbeiten sind ohne Klick per Maus erreichbar. Parallele Bildimporte werden in der gemeinsamen Zeitprognose korrekt berücksichtigt.
- Die Dateinamenseite konzentriert sich auf Auswahl und Umbenennung und führt bei Bedarf direkt zum Benennungsschema. Dessen Regeln für den Personen-Anhang und mehrere erkannte Personen sind klar getrennt und werden gemeinsam in einer verständlichen Vorschau gezeigt.
- Gesichts-Vorschaubilder werden im Hintergrund vorbereitet, wodurch Prüfansichten und die Navigation in großen Bibliotheken schneller reagieren.
- Die Windows-Installer enthalten konsistente Versions- und Projektangaben sowie überprüfbare Prüfsummen und Herkunftsnachweise.

### Behoben

- In „Personen korrigieren“ ist die Bearbeitung des Personennamens sofort erreichbar und nicht mehr vom vorherigen Öffnen der Gruppen-Umbenennung abhängig.
- Zuordnungen und Cluster bleiben auch bei parallelen Importen, späteren Korrekturen und automatischen Neu-Gruppierungen konsistent.
- Doppelte, verschobene oder ersetzte Bilddateien sowie ältere Datenbankstände werden zuverlässiger erkannt und repariert.
- Hintergrundarbeiten geben manuellen Änderungen Vorrang und blockieren die Oberfläche nicht länger als nötig.
