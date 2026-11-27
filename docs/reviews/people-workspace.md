# UI-Review der Personenansichten

Geprüft wurde Face Manager in Chromium bei 1280 × 720, 1024 × 640, 1024 × 600 und 940 × 620 CSS-Pixeln. Die isolierte SQLite-Bibliothek enthielt 122 Personen, 550 Gruppen, 3.718 Gesichtsausschnitte und 38 berechnete Vorschläge. Das Archiv enthielt je 122 Treffer pro Kategorie. Die Bibliothek verwendet öffentliche Beispielbilder mit künstlich erzeugten Namen, Wiederholungen und Zuordnungen. Für den optionalen Abgleich wurde eine Freigabe mit kontrollierten Antworten simuliert.

## Befunde nach Ansicht

| Ansicht | Größtes Problem im Ausgangsstand | Änderung |
| --- | --- | --- |
| **Vorschläge** | Die Sammelfunktion nahm oben viel Platz ein. Vier gleichrangige Aktionen je Karte erschwerten das Erkennen des nächsten Schritts. Im kurzen Fenster lagen die ersten Ausschnitte unterhalb der sichtbaren Fläche. | Die Vergleichsfotos, der Auswahlumfang und „Auswahl zuordnen“ stehen zusammen. „Gruppe prüfen“ bleibt direkt sichtbar. Originalgruppe und Verwerfen stehen unter „Weitere Aktionen“. Die Sammelleiste nennt die tatsächlich markierten Vorschläge und Gesichter. |
| **Gruppe prüfen** | Der hohe Dialog stellte Vergleichsfotos, Auswahl und Bestätigung weit auseinander. Auf kurzen Fenstern musste man trotz weniger Ausschnitte scrollen. | Die Ausschnitte und Auswahlfelder brauchen weniger Höhe. Die Bestätigung bleibt erreichbar, und Escape kehrt zur auslösenden Schaltfläche zurück. |
| **Ohne Namen** | Die Vorschau und Liste konkurrierten um Breite. Beim Öffnen eines weiter unten stehenden Suchtreffers konnten sehr viele Gruppen zwischen Anfang und Ziel Teil der langen Seite werden. Aktionen waren auf mehrere Zeilen verteilt. | Kleine und niedrige Fenster zeigen mit einem Klick die Gruppe auf voller Breite. „Zur Gruppenliste“ stellt Suchbegriff, Scrollposition und Fokus wieder her. Die Auswahl lässt sich auch weiter unten in der Gruppe direkt bearbeiten. |
| **Personen korrigieren** | Die Suche aus „Ohne Namen“ konnte alle Personen wegfiltern. Zwei Stiftsymbole standen für unterschiedliche Eingriffe. Beim Lösen aus einer Gruppe war der Umfang einer gruppenübergreifenden Auswahl unklar. | Jeder Prüfbereich beginnt mit einer passenden Suche. „Person bearbeiten“ ist ausgeschrieben. Gruppenbezogene Aktionen stehen zusammen und der Umfang beim Lösen ist am Ort der Aktion erklärt. |
| **Aussortiert – Übersicht** | Der Wechsel aus der Personensuche konnte die Archivkategorien ausblenden. Die Kategorien und ihre Gruppen lagen unter redundanten Überschriften. | Eine frische Suche zeigt beide Archivkategorien. Die Kategorieauswahl ist als gedrückte Schaltfläche erkennbar, und die Gruppen beginnen weiter oben. |
| **Unbekannte Personen** | Auf Kategorieebene standen mehrere deaktivierte Aktionen vor den Porträts. Die Unterscheidung zwischen ganzer Gruppe und markierten Ausschnitten war vor der Auswahl schwer erkennbar. | Ein kurzer Hinweis erklärt zuerst die nötige Auswahl. „Erneut prüfen“ ist die direkte Archivaktion. Nach der Auswahl zeigt die Zuordnung ihren Umfang. |
| **Keine Gesichter** | Ein direkter Aufruf konnte zunächst die andere Archivkategorie zeigen. Die Aktion für den bereits vergebenen Status wurde erneut angeboten. | Der Direktlink hat Vorrang vor der Standardkategorie. In jeder Archivkategorie stehen nur die Aktionen zur Verfügung, die den Status ändern können. Das Original lässt sich zum Prüfen separat öffnen. |
| **Abgleich prüfen** | Auf schmalen Fenstern standen die beiden Ausschnitte untereinander, die zugehörigen Aktionen davon getrennt. Die Ursache für eine gesperrte Entscheidung war schwer zu erkennen. | Die Ausschnitte stehen in zwei Spalten. Jede Entscheidung hat ihre eigene Aktion, und der Fall nennt den Grund bei gesperrter Übernahme. „Zur Fallliste“ bringt den Fokus zurück. |
| **Änderungsverlauf und Originalfoto** | Das Originalfoto war über ein kleines, mausabhängiges Symbol in der Auswahlfläche versteckt. Der Fokus ging beim Schließen verloren. | Auswahl und Originalfoto sind getrennt bedienbar. Das Original hat ein dauerhaft sichtbares Ziel, und nach Escape bleibt der Fokus am auslösenden Foto. Der Änderungsverlauf bleibt über „Änderungen“ beziehungsweise „Letzte Änderungen“ erreichbar. |

## Messpunkte vor und nach der Änderung

| Fenster | Messpunkt | Vorher | Nachher |
| --- | --- | ---: | ---: |
| 1280 × 720 | Oberkante des ersten Vorschlagsausschnitts | 561 px | 413 px |
| 1280 × 720 | Oberkante des ersten Gesichts unter „Ohne Namen“ | 541 px | 421 px |
| 1280 × 720 | Sichtbare Gesichter unter „Ohne Namen“ | 4 | 10 |
| 1024 × 640 | Oberkante des ersten Vorschlagsausschnitts | 653 px | 442 px |
| 1024 × 640 | Seitengesamthöhe nach Öffnen von „Testgruppe 137“ | 93.647 px | 594 px |
| 1024 × 600 | Oberkante des ersten Ausschnitts in der Archivkategorie | 459 px | 304 px |

Die Pixelwerte stammen aus derselben Bibliothek und demselben Browser. Die Seitengesamthöhe beschreibt den Scrollbereich einschließlich der Platzhalter für noch nicht geladene Gruppen.

## Funktionsprüfung

Auf einer separaten Kopie der großen Bibliothek wurden ein Vorschlag angenommen, ein einzelnes Gesicht über die Gruppenauswahl zugewiesen und die Zuweisung rückgängig gemacht. Aus beiden Archivkategorien wurde je ein Treffer über „Erneut prüfen“ in die offene Prüfung zurückgeholt. Die Browsertests prüfen außerdem die stabile Vorschlagsliste während einer Aktualisierung, den direkten Archivlink, die separate Originalansicht und den Tastaturfokus. Der Abgleich wurde mit simulierter Freigabe in verbundenem, schreibgeschütztem und veraltetem Zustand geprüft.

## Gestaltungsgrundlagen

- Auf kurzen oder schmalen Fenstern arbeitet die App mit einer Liste und einem eigenen Detailbildschirm. Beide bleiben auf größeren Fenstern nebeneinander nutzbar. [Android Developers zu adaptiver Listen- und Detailansicht](https://developer.android.com/develop/adaptive-apps/guides/canonical-layouts)
- Häufige Aktionen bleiben direkt am Gegenstand. Seltene oder kontextabhängige Aktionen werden eingeblendet, wenn sie gebraucht werden. Der Umfang einer Sammelaktion steht bei der Auswahl. [NN/g zu Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/) · [IBM Carbon zu Sammelaktionen](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)
- Textlabels und Bildausschnitte erleichtern das Wiedererkennen. Ein Dialog gibt den Tastaturfokus zurück, und bedienbare Ziele bleiben getrennt und sichtbar. [NN/g zu Wiedererkennen](https://www.nngroup.com/articles/recognition-and-recall/) · [W3C zu Zielgröße](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum) · [W3C zu sichtbarem Fokus](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum) · [W3C zum Dialogfokus](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)

- Lightroom Classic trennt unbenannte Gruppen von Namensvorschlägen. digiKam hält die bestätigte Ansicht frei von den Schaltflächen der Erstprüfung. Beide Abläufe stützen die Trennung nach Aufgabe in Face Manager. [Adobe Lightroom Classic](https://helpx.adobe.com/de/lightroom-classic/desktop/organize-photos-in-lightroom-classic/face-recognition.html) · [digiKam People View](https://docs.digikam.org/en/left_sidebar/people_view.html)
