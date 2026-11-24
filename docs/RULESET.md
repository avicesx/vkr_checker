# Набор правил нормоконтроля ВКР

Каталог наборов `backend/rule_engine/rules/vkr_test_rules.json` (версия 4.1.0) и профиля УрГПУ `backend/rule_engine/rules/profiles/urgpu.json` (версия 2.1.0) с таблицей покрытия 67 требований методички УрГПУ.

## Сводка

- правил в наборе по умолчанию: **52**, кодов: **36**
- значений по умолчанию общие для ВКР; требования УрГПУ живут в профиле: **32 переопределений**
- объединённый профиль УрГПУ: **60 правил** (8 добавлены профилем)
- покрыто требований методички: **61 из 67**, не покрыто: 6

## Как устроены наборы

Каждое правило обязано иметь семь полей: `rule_id`, `code`, `strictness`, `message`, `quote`, `target` и `params`. `quote` — дословная формулировка методички, на которую опирается проверка. Базовый набор описывает общие требования ВКР, профиль вуза задаёт только отличия: поле `extends` указывает, поверх какого набора применяются переопределения, и правила профиля заменяют одноимённые правила базы, а новые `rule_id` добавляются к набору. Все три профиля (`mpgu`, `urgpu`, `urfu`) лежат в `backend/rule_engine/rules/profiles/`, расширяют набор `vkr-rule-engine-test` и задают только переопределения.

| Профиль | Файл | `extends` | Переопределений | Правил после слияния |
|---|---|---|---|---|
| `mpgu` | `backend/rule_engine/rules/profiles/mpgu.json` | `vkr-rule-engine-test` | 5 | 52 |
| `urgpu` | `backend/rule_engine/rules/profiles/urgpu.json` | `vkr-rule-engine-test` | 32 | 60 |
| `urfu` | `backend/rule_engine/rules/profiles/urfu.json` | `vkr-rule-engine-test` | 6 | 52 |

## Что в базе нейтрально, а что принадлежит УрГПУ

| `rule_id` | Решение |
|---|---|
| `caption_placement_table` | база ставит подпись справа, как требует цитата методички и профиль УрГПУ; вариант «слева с тире» (ГОСТ 7.32, МПГУ) остаётся возможным переопределением профиля |
| `bibliography_format_dot_dash` | база разрешает тире «. —» между элементами описания по ГОСТ 7.32; запрет — только профиль УрГПУ |
| `toc_format_default` | оставлено нейтральным: отточие в базе разрешено, профиль УрГПУ его запрещает |
| `caption_placement_figure` | оставлено нейтральным: база пишет «Рисунок» по ГОСТ, профиль — «Рис.» |
| `caption_format_figures` | оставлено нейтральным: база разделяет номер и название тире по ГОСТ, профиль — точкой |
| `list_format_default` | оставлено нейтральным: база разрешает дефис и тире на первом уровне, профиль — только дефис |
| `bibliography_count` | оставлено нейтральным: база требует 20 источников, профиль УрГПУ — 35 |
| `document_volume` | оставлено нейтральным: порог 60–90 страниц пришёл из набора 3.0.0 и не является требованием УрГПУ |
| `font_table_min` | перенесено в профиль: минимальный кегль 10 пт — требование УрГПУ, в базе остаётся только 12 пт |

## Каталог набора по умолчанию (4.1.0)

| № | `rule_id` | `code` | `scope` | `strictness` | `params` | Требование | `quote` |
|---|---|---|---|---|---|---|---|
| 1 | `font_body_text` | `font` | `body_text` | `error` | `{"family": "Times New Roman", "size_pt": 14.0, "size_tolerance_pt": 0.5}` | №1 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) |
| 2 | `indent_first_line` | `indent` | `body_text` | `error` | `{"first_line_mm": 12.5, "tolerance_mm": 0.5}` | №5 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) |
| 3 | `alignment_body_text` | `alignment` | `body_text` | `error` | `{"alignment": "justify"}` | №4 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) |
| 4 | `line_spacing_body` | `line_spacing` | `body_text` | `error` | `{"ratio": 1.5, "tolerance": 0.25}` | №3 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) |
| 5 | `microtypography_prose` | `microtypography` | `body_paragraphs` | `warning` | `{"checks": ["wrong_quotes", "wrong_dashes", "multiple_spaces", "space_before_punctuation"]}` | №35 | При дословном цитировании мысль автора заключается в кавычки и приводится в той грамматической форме, в которой дана в первоисточнике. |
| 6 | `microtypography_nbsp` | `microtypography` | `body_paragraphs` | `info` | `{"checks": ["missing_nbsp"]}` | №35 | При оформлении ссылки используют сокращения. Например: рис. 2. |
| 7 | `page_margins` | `margin` | `sections` | `error` | `{"left_mm": 30.0, "right_mm": 15.0, "top_mm": 20.0, "bottom_mm": 20.0, "tolerance_mm": 1.0}` | №7 | Поля (см): левое – 3 , правое – 2, верхнее и нижнее – 2,5. |
| 8 | `page_number_position` | `page_number_position` | `headers_footers` | `error` | `{"zone": "footer", "alignment": "center"}` | №8, №9, №10, №47 | Нумерация страниц, включая приложения, начинается с Титульного листа, которому присваивается № 1, но на странице он не ставится. |
| 9 | `page_format` | `page_format` | `sections` | `error` | `{"width_mm": 210.0, "height_mm": 297.0, "tolerance_mm": 1.0, "orientation": "portrait"}` | №10 | Иллюстрации и таблицы могут располагаться на отдельных листах, такие страницы включаются в общую нумерацию. |
| 10 | `widow_control_body` | `widow_control` | `body_text` | `warning` | `{"enabled": true}` | №16 | Не оставлять заголовок параграфа внизу листа, если за ним не следует текст. |
| 11 | `heading_levels` | `heading_levels` | `headings` | `error` | `{"first_level": 1, "allow_skipped_levels": false, "max_level": 3}` | №11 | Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2 |
| 12 | `heading_numbering` | `heading_numbering` | `headings` | `error` | `{"check_sequence": true, "check_parent_number": true, "check_depth_matches_level": true, "number_ending": "any", "require_number_for_roles": ["main_chapter", "sub_section"]}` | №11 | Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2 |
| 13 | `heading_format_text` | `heading_format` | `headings` | `error` | `{"trailing_dot": "forbid"}` | №12, №13 | не подчеркивая, без точки в конце. |
| 14 | `heading_font` | `heading_format` | `headings` | `warning` | `{"font": {"family": "Times New Roman", "size_pt": 14.0, "bold": true}}` | №12 | Заголовки разделов (глав): оформляются жирным шрифтом, заглавными буквами по центру, не подчеркивая, без точки в конце. |
| 15 | `heading_alignment` | `heading_format` | `headings` | `warning` | `{"alignment": "center"}` | №12 | Заголовки разделов (глав): оформляются жирным шрифтом, заглавными буквами по центру, не подчеркивая, без точки в конце. |
| 16 | `heading_page_break` | `heading_page_break` | `headings` | `warning` | `{"roles": ["main_chapter", "introduction", "conclusion", "bibliography", "appendix"]}` | №15, №17 | Глава: с новой страницы (!), параграфы продолжать на той же странице |
| 17 | `heading_keep_next` | `keep_next` | `headings` | `warning` | `{"enabled": true, "roles": ["main_chapter", "sub_section"]}` | №16 | Не оставлять заголовок параграфа внизу листа, если за ним не следует текст. |
| 18 | `heading_hyphenation_default` | `heading_hyphenation` | `headings` | `error` | `{"forbid_hyphenation": true, "forbid_abbreviations": false}` | №14 | Перенос слов и аббревиатуры в заголовках не допускаются. |
| 19 | `required_sections` | `required_sections` | `document_structure` | `error` | `{"sections": [{"label": "введение", "role": "introduction", "titles": ["ВВЕДЕНИЕ"], "required": true}, {"label": "основные разделы", "role": "main_chapter", "required": true}, {"label": "заключение", "role": "conclusion", "titles": ["ЗАКЛЮЧЕНИЕ"], "required": true}, {"label": "список литературы", "role": "bibliography", "required": true}, {"label": "приложения", "role": "appendix", "titles": ["ПРИЛОЖЕНИЕ", "ПРИЛОЖЕНИЯ"], "required": false}]}` | №60, №61 | Структурными элементами ВКР являются: титульный лист, оглавление, введение, основная часть, заключение, список литературы, приложения. |
| 20 | `required_sections_chapters` | `required_sections` | `document_structure` | `error` | `{"sections": [], "min_main_chapters": 2}` | №62, №63 | Основная часть, как правило, не может состоять лишь из одной главы, она состоит из нескольких (двух-трех) глав. |
| 21 | `toc` | `toc` | `toc` | `error` | `{"require_toc": true, "require_automated": false, "require_page_numbers": true, "check_page_numbers": false, "check_titles": true, "require_dot_leader": false, "check_completeness": true, "max_depth": 2, "required_entries": [{"label": "введение", "role": "introduction", "titles": ["ВВЕДЕНИЕ"]}, {"label": "раздел", "role": "main_chapter"}, {"label": "подраздел", "role": "sub_section"}, {"label": "заключение", "role": "conclusion", "titles": ["ЗАКЛЮЧЕНИЕ"]}, {"label": "список литературы", "role": "bibliography"}]}` | №21, №22, №23 | Названия разделов в оглавлении должны точно совпадать с заголовками в тексте. Запрещается сокращать название глав и параграфов. |
| 22 | `toc_format_default` | `toc` | `toc` | `error` | `{"require_toc": true, "entry_alignment": "left", "entry_bold": false, "capitalize_entry": true, "forbid_dot_leader": false}` | №21 | Содержание: все названия с левого края страницы, с Заглавной буквы; нежирно; не делать: ….. (строчки точек до номера страницы). |
| 23 | `document_volume` | `document_volume` | `document` | `warning` | `{"min_pages": 60, "max_pages": 90, "min_words": 8000, "max_words": 18000}` | №60 | Структурными элементами курсовой работы являются: титульный лист, оглавление, введение, основная часть, заключение, список литературы, приложения. |
| 24 | `equation_numbering` | `equation_numbering` | `document` | `warning` | `{"require_numbering": true}` | №11 | Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2 |
| 25 | `bibliography_links_resolved` | `bibliography_link` | `bibliography` | `error` | `{"allow_unresolved": false}` | №59 | URL: http://www.voennoepravo.ru/node/2149 (дата обращения: 19.09.2007). |
| 26 | `bibliography_all_cited` | `bibliography_cited` | `bibliography` | `error` | `{"require_all_cited": true}` | №56 | В список литературы могут быть включены только те источники, которые использовались при написании курсовой работы или на которые есть ссылки в тексте |
| 27 | `bibliography_count` | `bibliography_count` | `bibliography` | `warning` | `{"min_sources": 20}` | №54, №55 | Список литературы должен включать не менее 15-17 источников в полном библиографическом описании. |
| 28 | `bibliography_order` | `bibliography_order` | `bibliography` | `error` | `{"start_at": 1, "allow_gaps": false, "allow_duplicates": false}` | №51, №52, №53 | Список литературы: сквозная нумерация, группы не выделяются |
| 29 | `bibliography_recency` | `bibliography_recency` | `bibliography` | `warning` | `{"min_recent_share": 0.3, "recent_years": 5, "min_year_coverage": 0.5}` | №56 | В список литературы могут быть включены только те источники, которые использовались при написании курсовой работы или на которые есть ссылки в тексте |
| 30 | `bibliography_format_electronic` | `bibliography_format` | `bibliography` | `error` | `{"categories": ["electronic_resource"], "require_url": true, "require_access_date": true}` | №59 | URL: http://www.voennoepravo.ru/node/2149 (дата обращения: 19.09.2007). |
| 31 | `bibliography_format_article` | `bibliography_format` | `bibliography` | `error` | `{"categories": ["journal_article"], "require_double_slash": true}` | №57, №58 | Кольцова Е.Ю. Производство новостей: скрытые механизмы контроля // Журнал социологии и социальной антропологии. 1999. №3. С.87-104. |
| 32 | `bibliography_format_dot_dash` | `bibliography_format` | `bibliography` | `error` | `{"require_year": true, "require_pages": true, "forbid_dot_dash": false}` | №57 | Валукин М.Е. Эволюция движений в мужском классическом танце. М.: ГИТИС, 2006. 251 с. |
| 33 | `caption_numbering_figures` | `caption_numbering` | `captions` | `error` | `{"entity_type": "figure", "scheme": "continuous_arabic", "allow_gaps": false, "allow_duplicates": false, "allow_out_of_order": false}` | №44 | Все иллюстрации именуются рисунками, нумерация сквозная. |
| 34 | `caption_numbering_tables` | `caption_numbering` | `captions` | `error` | `{"entity_type": "table", "scheme": "continuous_arabic", "allow_gaps": false, "allow_duplicates": false, "allow_out_of_order": false}` | №28, №29 | Нумерация таблиц: арабскими цифрами, без знака номер и точки в конце, сквозная по всему тексту работы или самостоятельной в каждом разделе, за исключением приложений (в приложении нумерация своя) |
| 35 | `caption_format_figures` | `caption_format` | `captions` | `error` | `{"entity_type": "figure", "separator": "dash", "require_trailing_dot": false}` | №45 | Номера иллюстраций и их заглавия пишутся внизу под изображением, обозначаются арабскими цифрами без номера после слова «Рис.» |
| 36 | `caption_format_tables` | `caption_format` | `captions` | `error` | `{"entity_type": "table", "separator": "dash"}` | №26 | Название таблицы располагается между ее нумерацией и собственным содержанием и пишется с заглавной буквы без точки в конце. |
| 37 | `caption_reference_figures` | `caption_reference` | `captions` | `error` | `{"entity_type": "figure", "required": true}` | №43 | Остальной иллюстрационный материал располагают в приложениях. |
| 38 | `caption_reference_tables` | `caption_reference` | `captions` | `error` | `{"entity_type": "table", "required": true}` | №43 | Остальной иллюстрационный материал располагают в приложениях. |
| 39 | `caption_reference_appendix` | `caption_reference` | `captions` | `error` | `{"entity_type": "appendix", "required": true}` | №50 | Необходимо следить, чтобы в тексте работы обязательно были ссылки на соответствующее приложение. |
| 40 | `caption_placement_table` | `caption_placement` | `captions` | `error` | `{"entity_type": "table", "position": "above_table", "alignment": "right", "forbid_quotes": true, "prefix_word": "Таблица", "require_number": true}` | №25 | Слово «Таблица» без сокращения и кавычек пишется в правом верхнем углу над самой таблицей и ее заголовком. |
| 41 | `caption_placement_figure` | `caption_placement` | `captions` | `error` | `{"entity_type": "figure", "position": "below_object", "alignment": "center", "prefix_word": "Рисунок", "title_case": "capitalized", "require_number": true}` | №45 | Номера иллюстраций и их заглавия пишутся внизу под изображением, обозначаются арабскими цифрами без номера после слова «Рис.» |
| 42 | `caption_placement_appendix` | `caption_placement` | `captions` | `error` | `{"entity_type": "appendix", "alignment": "center", "require_number": true, "title_case": "capitalized"}` | №46 | Приложение: по центру, нумеруется порядковым номером, заголовок пишется ниже по центру с Заглавной буквы. |
| 43 | `alignment_captions` | `alignment` | `captions` | `error` | `{"alignment": "center"}` | №45 | Номера иллюстраций и их заглавия пишутся внизу под изображением, обозначаются арабскими цифрами без номера после слова «Рис.» |
| 44 | `alignment_table_cells` | `alignment` | `table_cells` | `error` | `{"alignment": "left"}` | №30 | Таблицу со всех сторон ограничить линиями, точно соответствовать размерам полей |
| 45 | `font_table_cells` | `font` | `table_cells` | `error` | `{"family": "Times New Roman", "size_pt": 12.0, "size_tolerance_pt": 0.5}` | №31 | Текст внутри таблицы может выполняться меньшим шрифтом (но не меньше 10pt). |
| 46 | `inline_reference_format_body` | `inline_reference_format` | `body_text` | `error` | `{"ref_type": "bibliography", "style": "square_brackets", "require_page": true, "require_number": true}` | №34 | Ссылки на цитаты и заимствованные данные в тексте: [3, с. 15] (номер в списке использованных источников и номер страницы, с которой взята информация). |
| 47 | `table_layout_borders` | `table_layout` | `table_cells` | `error` | `{"require_all_sides_borders": true, "require_full_text_width": true, "width_tolerance_mm": 1.0}` | №30 | Таблицу со всех сторон ограничить линиями, точно соответствовать размерам полей |
| 48 | `table_continuation_default` | `table_continuation` | `table_cells` | `error` | `{"require_label": true, "forbid_bottom_border_on_break": true, "repeat_header": true, "forbid_repeated_caption": true}` | №33 | При переносе части таблицы на другой лист: нижней линии нет, на след. листе слева: Продолжение /или Окончание/ таблицы …. /номер/ |
| 49 | `table_header_format_default` | `table_header_format` | `table_cells` | `error` | `{"first_letter": "capitalize", "forbid_trailing_dot": true, "subheader_case": "any"}` | №32 | Заголовки граф и строк табл. (без точки в конце): с Заглавной буквы в ед. числе, подзаголовки – со строчной (маленькой) буквы, если составляют одно предложение с заголовком, или с Заглавной, если имеют самостоятельное значение. |
| 50 | `list_format_default` | `list_format` | `body_paragraphs` | `error` | `{"level_markers": [{"level": 0, "allowed": ["hyphen", "dash"]}, {"level": 1, "allowed": ["arabic_parenthesis", "russian_letter_parenthesis"]}], "forbid_other_markers": true}` | №24 | Перечисления: дефис (другие маркеры не допустимы), при необходимости – русские буквы со скобкой: б) и) /кроме ё,з,о,ч,ы,ъ,ь,й/; для дальнейшей детализации – арабские цифры: 2) |
| 51 | `appendix_order_default` | `appendix_order` | `document_structure` | `error` | `{"order": "by_first_mention", "new_page_each": true}` | №48 | Приложения в порядке их упоминания в тексте, каждое с нового листа. |
| 52 | `title_page_content_default` | `title_page_content` | `document_structure` | `error` | `{"required_fields": ["министерство", "вуз", "кафедра", "вид работы", "тема", "автор", "руководитель", "город", "год"]}` | №64, №65 | Титульный лист курсовой работы должен содержать: … полное наименование министерства РФ, вуза, института, кафедры, где выполнена работа; … вид исследовательской работы (курсовая работа); … название темы курсовой работы; … сведения об авторе (Ф.И.О. студента, курс, номер группы и отделение); … сведения о научном руководителе (Ф.И.О., ученая степень, должность преподавателя, ученое звание). … строка для проставления оценки; … город, год написания. |

## Профиль УрГПУ (2.1.0, `extends: vkr-rule-engine-test`)

| `rule_id` | `code` | `params` профиля | Требование |
|---|---|---|---|
| `font_body_text` | `font` | `{"family": "Times New Roman", "size_pt": 14.0, "size_tolerance_pt": 0.5, "bold": false}` | №1 |
| `indent_first_line` | `indent` | `{"first_line_mm": 12.5, "tolerance_mm": 2.5}` | №5 |
| `page_margins` | `margin` | `{"left_mm": 30.0, "right_mm": 20.0, "top_mm": 25.0, "bottom_mm": 25.0, "tolerance_mm": 3.0}` | №7 |
| `page_number_position` | `page_number_position` | `{"zone": "footer", "alignment": "center", "skip_first_page": true, "require_arabic_digits": true, "forbid_trailing_dot": true, "require_continuous": true, "include_appendices": true}` | №8, №9, №10, №47 |
| `heading_numbering` | `heading_numbering` | `{"check_sequence": true, "check_parent_number": true, "check_depth_matches_level": true, "number_ending": "without_dot", "require_number_for_roles": ["main_chapter", "sub_section"]}` | №11 |
| `heading_page_break` | `heading_page_break` | `{"roles": ["main_chapter", "introduction", "conclusion", "bibliography", "appendix"], "forbid_new_page_roles": ["sub_section"]}` | №15, №17 |
| `heading_format_text` | `heading_format` | `{"case": "uppercase", "trailing_dot": "forbid", "underline": "forbid"}` | №12, №13 |
| `required_sections` | `required_sections` | `{"sections": [{"label": "введение", "role": "introduction", "titles": ["введение"], "required": true}, {"label": "основная часть", "role": "main_chapter", "required": true}, {"label": "заключение", "role": "conclusion", "titles": ["заключение"], "required": true}, {"label": "список литературы", "role": "bibliography", "titles": ["список литературы", "список использованных источников"], "required": true}, {"label": "приложения", "role": "appendix", "titles": ["приложения", "приложение"], "required": false}]}` | №60, №61 |
| `toc` | `toc` | `{"require_toc": true, "require_page_numbers": true, "check_titles": true, "check_completeness": true, "max_depth": 2, "required_entries": [{"label": "введение", "role": "introduction", "require_number": false}, {"label": "заключение", "role": "conclusion", "require_number": false}, {"label": "список литературы", "role": "bibliography", "require_number": false}, {"label": "приложения", "role": "appendix", "require_number": false}]}` | №21, №22, №23 |
| `heading_hyphenation_default` | `heading_hyphenation` | `{"forbid_hyphenation": true, "forbid_abbreviations": true}` | №14 |
| `toc_format_default` | `toc` | `{"require_toc": true, "forbid_dot_leader": true, "entry_alignment": "left", "entry_bold": false, "capitalize_entry": true}` | №21 |
| `list_format_default` | `list_format` | `{"level_markers": [{"level": 0, "allowed": ["hyphen"]}, {"level": 1, "allowed": ["russian_letter_parenthesis"]}, {"level": 2, "allowed": ["arabic_parenthesis"]}], "forbid_other_markers": true}` | №24 |
| `caption_placement_figure` | `caption_placement` | `{"entity_type": "figure", "position": "below_object", "alignment": "center", "prefix_word": "Рис.", "title_case": "capitalized", "require_number": true}` | №45 |
| `caption_format_tables` | `caption_format` | `{"entity_type": "table", "separator": "dot", "require_trailing_dot": false, "capitalize_first_letter": true}` | №26 |
| `caption_numbering_tables` | `caption_numbering` | `{"entity_type": "table", "scheme": "any_of", "schemes": ["continuous_arabic", "section_based"], "appendix": "own", "allow_gaps": false, "allow_duplicates": false, "allow_out_of_order": false}` | №28, №29 |
| `caption_numbering_single_table` | `caption_numbering` | `{"entity_type": "table", "skip_number_when_single": true}` | №29 |
| `table_header_format_default` | `table_header_format` | `{"first_letter": "capitalize", "forbid_trailing_dot": true, "subheader_case": "lowercase_if_continuation"}` | №32 |
| `inline_reference_format_body` | `inline_reference_format` | `{"ref_type": "bibliography", "style": "square_brackets", "require_number": true, "require_page": true}` | №34 |
| `inline_reference_format_quoted_from` | `inline_reference_format` | `{"ref_type": "bibliography", "style": "quoted_from", "patterns": ["\\[\\s*Цит\\.\\s*по"]}` | №37 |
| `inline_reference_format_author` | `inline_reference_format` | `{"ref_type": "bibliography", "style": "author_then_number_and_page"}` | №38 |
| `inline_reference_format_ibid` | `inline_reference_format` | `{"ref_type": "bibliography", "style": "ibid", "patterns": ["\\[\\s*[Тт]ам\\s+же"]}` | №39 |
| `inline_reference_format_section` | `inline_reference_format` | `{"ref_type": "section", "style": "parentheses_see"}` | №40 |
| `inline_reference_format_figure` | `inline_reference_format` | `{"ref_type": "figure", "require_abbrev": "рис."}` | №41 |
| `inline_reference_format_appendix` | `inline_reference_format` | `{"ref_type": "appendix", "style": "parentheses_see", "require_abbrev": "прил."}` | №49 |
| `bibliography_order` | `bibliography_order` | `{"start_at": 1, "allow_gaps": false, "allow_duplicates": false, "allow_groups": false, "sort": "alphabetical"}` | №51, №52, №53 |
| `bibliography_count` | `bibliography_count` | `{"min_sources": 35}` | №54, №55 |
| `bibliography_format_article` | `bibliography_format` | `{"categories": ["journal_article"], "require_double_slash": true, "require_year": true, "require_pages": true}` | №57, №58 |
| `required_sections_chapters` | `required_sections` | `{"sections": [], "min_main_chapters": 2, "max_main_chapters": 3}` | №62, №63 |
| `title_page_content_default` | `title_page_content` | `{"required_fields": ["министерство", "вуз", "институт", "кафедра", "вид работы", "тема", "автор", "руководитель", "оценка", "город", "год"], "work_type": "ВКР"}` | №64, №65 |
| `font_table_min` | `font` | `{"min_size_pt": 10.0, "size_tolerance_pt": 0.5}` | №31 |
| `bibliography_format_dot_dash` | `bibliography_format` | `{"require_year": true, "require_pages": true, "forbid_dot_dash": true}` | №57 |
| `caption_format_figures` | `caption_format` | `{"entity_type": "figure", "separator": "dot", "require_trailing_dot": false}` | №45 |

## Покрытие 67 требований методички УрГПУ

| № | Требование (дословно) | Правило | Статус |
|---|---|---|---|
| 1 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) | `font_body_text` | покрыто |
| 2 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) | `font_body_text` | покрыто базовым правилом шрифта (14 pt с допуском 0.5) |
| 3 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) | `line_spacing_body` | покрыто |
| 4 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) | `alignment_body_text` | покрыто |
| 5 | Шрифт: 14pt Times New Roman, 1,5 интервала, по ширине, отступ 1,25 (красная строка) | `indent_first_line` | покрыто |
| 6 | для выделения в тексте можно использовать курсив; полужирный не допускается. | `font_body_text` | покрыто профилем УрГПУ: `font_body_text` с `bold: false`; базовый набор полужирный не проверяет |
| 7 | Поля (см): левое – 3 , правое – 2, верхнее и нижнее – 2,5. | `page_margins` | покрыто |
| 8 | Нумерация страниц, включая приложения, начинается с Титульного листа, которому присваивается № 1, но на стр… | `page_number_position` | работает в PDF-режиме; в DOCX — `not_assessed` с текстом «нужен PDF-режим» |
| 9 | Оформляется внизу по центру арабскими цифрами без точки. | `page_number_position` | работает в PDF-режиме; в DOCX — `not_assessed` с текстом «нужен PDF-режим» |
| 10 | Иллюстрации и таблицы могут располагаться на отдельных листах, такие страницы включаются в общую нумерацию. | `page_number_position` | работает в PDF-режиме; в DOCX — `not_assessed` с текстом «нужен PDF-режим» |
| 11 | Нумерация разделов, подразделов, пунктов и т.д.: арабские цифры, без точки после последней цифры. Образец: 1.2 | `heading_numbering`, `heading_levels` | покрыто |
| 12 | Заголовки разделов (глав): оформляются жирным шрифтом, заглавными буквами по центру, не подчеркивая, без то… | `heading_format_text`, `heading_font`, `heading_alignment` | покрыто |
| 13 | не подчеркивая, без точки в конце. | `heading_format_text` | покрыто |
| 14 | Перенос слов и аббревиатуры в заголовках не допускаются. | `heading_hyphenation_default` | покрыто |
| 15 | Начинать новый параграф с новой страницы не нужно. | `heading_page_break` | покрыто |
| 16 | Не оставлять заголовок параграфа внизу листа, если за ним не следует текст. | `heading_keep_next`, `widow_control_body` | покрыто |
| 17 | Глава: с новой страницы (!), параграфы продолжать на той же странице | `heading_page_break` | покрыто |
| 18 | Расстояние от названия параграфа до текста - 1 межстрочный интервал, от названия главы и названия параграфа… | `heading_format_text` | покрыто параметром `space_after_lines`; в наборы не включено: Word задаёт интервал пустой строкой — находки были бы ложными |
| 19 | Расстояние от названия параграфа до текста - 1 межстрочный интервал, от названия главы и названия параграфа… | `heading_format_text` | покрыто параметром `space_after_lines`; в наборы не включено: Word задаёт интервал пустой строкой — находки были бы ложными |
| 20 | Расстояние от названия параграфа до текста - 1 межстрочный интервал, от названия главы и названия параграфа… | `heading_format_text` | покрыто параметром `space_before_lines`; в наборы не включено: Word задаёт интервал пустой строкой — находки были бы ложными |
| 21 | Содержание: все названия с левого края страницы, с Заглавной буквы; нежирно; не делать: ….. (строчки точек … | `toc`, `toc_format_default` | покрыто |
| 22 | Названия разделов в оглавлении должны точно совпадать с заголовками в тексте. Запрещается сокращать названи… | `toc` | покрыто |
| 23 | Введение, заключение, список литературы, приложения в содержании не нумеруются. | `toc` | покрыто |
| 24 | Перечисления: дефис (другие маркеры не допустимы), при необходимости – русские буквы со скобкой: б) и) /кро… | `list_format_default` | покрыто |
| 25 | Слово «Таблица» без сокращения и кавычек пишется в правом верхнем углу над самой таблицей и ее заголовком. | `caption_placement_table` | покрыто |
| 26 | Название таблицы располагается между ее нумерацией и собственным содержанием и пишется с заглавной буквы бе… | `caption_format_tables` | покрыто |
| 27 | Название таблицы, ее отдельных строк не должно содержать сокращений, аббревиатур, не оговоренных ранее в те… | — | не покрыто: не покрыто: проверка смысла сокращений требует языковой модели |
| 28 | Нумерация таблиц: арабскими цифрами, без знака номер и точки в конце, сквозная по всему тексту работы или с… | `caption_numbering_tables` | покрыто |
| 29 | если в тексте только одна таблица, то номер ей не присваивается и слово «таблица» не пишется. | `caption_numbering_tables`, `caption_numbering_single_table` | покрыто |
| 30 | Таблицу со всех сторон ограничить линиями, точно соответствовать размерам полей | `table_layout_borders`, `alignment_table_cells` | покрыто |
| 31 | Текст внутри таблицы может выполняться меньшим шрифтом (но не меньше 10pt). | `font_table_cells`, `font_table_min` (профиль) | покрыто |
| 32 | Заголовки граф и строк табл. (без точки в конце): с Заглавной буквы в ед. числе, подзаголовки – со строчной… | `table_header_format_default` | покрыто |
| 33 | При переносе части таблицы на другой лист: нижней линии нет, на след. листе слева: Продолжение /или Окончан… | `table_continuation_default` | покрыто |
| 34 | Ссылки на цитаты и заимствованные данные в тексте: [3, с. 15] (номер в списке использованных источников и н… | `inline_reference_format_body` | покрыто |
| 35 | При дословном цитировании мысль автора заключается в кавычки и приводится в той грамматической форме, в кот… | — | не покрыто: не покрыто: дословность цитаты проверяется по первоисточнику, а не по разбору |
| 36 | При недословном цитировании (пересказ, изложение точек зрения различных авторов своими словами) текст в кав… | — | не покрыто: не покрыто: дословность цитаты проверяется по первоисточнику, а не по разбору |
| 37 | Если текст цитируется не по первоисточнику, а по другому изданию, то следует оформлять так: [Цит. по кн.: 6… | `inline_reference_format_quoted_from` | покрыто |
| 38 | текст берется в кавычки, затем в скобках указывается фамилия автора, номер в списке литературы и страница. | `inline_reference_format_author` | покрыто |
| 39 | Далее в квадратных скобках принято писать [там же] или при цитировании [Там же. С. 309]. | `inline_reference_format_ibid` | покрыто |
| 40 | Ссылку в тексте на отдельный раздел работы, не входящий в строй данной фразы, заключают в круглые скобки, п… | `inline_reference_format_section` | покрыто |
| 41 | При оформлении ссылки используют сокращения. Например: рис. 2. | `inline_reference_format_figure` | покрыто |
| 42 | Иллюстрации: созданы средствами MS-Word, черно-белые, можно цветные. | — | не покрыто: не покрыто: в разборе нет нужного элемента, требуется новый парсер |
| 43 | Остальной иллюстрационный материал располагают в приложениях. | `caption_reference_figures`, `caption_reference_tables` | покрыто |
| 44 | Все иллюстрации именуются рисунками, нумерация сквозная. | `caption_numbering_figures` | покрыто |
| 45 | Номера иллюстраций и их заглавия пишутся внизу под изображением, обозначаются арабскими цифрами без номера … | `caption_placement_figure`, `caption_format_figures` | покрыто |
| 46 | Приложение: по центру, нумеруется порядковым номером, заголовок пишется ниже по центру с Заглавной буквы. | `caption_placement_appendix` | покрыто |
| 47 | Нумерация страниц, на которых даются приложения, должна быть сквозной и продолжать общую нумерацию страниц … | `page_number_position` | работает в PDF-режиме; в DOCX — `not_assessed` с текстом «нужен PDF-режим» |
| 48 | Приложения в порядке их упоминания в тексте, каждое с нового листа. | `appendix_order_default` | покрыто |
| 49 | При оформлении ссылки на приложение используют сокращения. Например: (см. прил. 2). | `inline_reference_format_appendix` | покрыто |
| 50 | Необходимо следить, чтобы в тексте работы обязательно были ссылки на соответствующее приложение. | `caption_reference_appendix` | покрыто |
| 51 | Список литературы: сквозная нумерация, группы не выделяются | `bibliography_order` | покрыто |
| 52 | Порядок групп в списке (не менее 35 источников): | `bibliography_order` | покрыто параметром `group_order`; в профиле выключено, потому что требование 51 запрещает группы |
| 53 | все источники информации указываются в алфавитном порядке, в том числе – электронные ресурсы: | `bibliography_order` | покрыто |
| 54 | Список литературы должен включать не менее 15-17 источников в полном библиографическом описании. | `bibliography_count` | покрыто |
| 55 | Список литературы должен включать не менее 35-37 источников в полном библиографическом описании. | `bibliography_count` | покрыто |
| 56 | В список литературы могут быть включены только те источники, которые использовались при написании курсовой … | `bibliography_all_cited`, `bibliography_recency` | покрыто |
| 57 | Валукин М.Е. Эволюция движений в мужском классическом танце. М.: ГИТИС, 2006. 251 с. | `bibliography_format_article`, `bibliography_format_dot_dash` | покрыто |
| 58 | Кольцова Е.Ю. Производство новостей: скрытые механизмы контроля // Журнал социологии и социальной антрополо… | `bibliography_format_article` | покрыто |
| 59 | URL: http://www.voennoepravo.ru/node/2149 (дата обращения: 19.09.2007). | `bibliography_format_electronic`, `bibliography_links_resolved` | покрыто |
| 60 | Структурными элементами курсовой работы являются: титульный лист, оглавление, введение, основная часть, зак… | `required_sections`, `document_volume` | титульный лист и оглавление проверяются правилами `title_page_content` и `toc` |
| 61 | Структурными элементами ВКР являются: титульный лист, оглавление, введение, основная часть, заключение, спи… | `required_sections` | разделы сопоставляются по семантической роли заголовка; титульный лист и оглавление — отдельными правилами |
| 62 | Основная часть, как правило, не может состоять лишь из одной главы, она состоит из нескольких (двух-трех) г… | `required_sections_chapters` | покрыто |
| 63 | Основная часть, как правило, не может состоять лишь из одной главы, она состоит из нескольких (трех) глав. | `required_sections_chapters` | покрыто частично: профиль задаёт 2–3 главы (вариант требования 62), здесь требуется ровно 3 |
| 64 | Титульный лист курсовой работы должен содержать: … полное наименование министерства РФ, вуза, института, ка… | `title_page_content_default` | покрыто частично: базовый набор проверяет 9 реквизитов, профиль — 11 (добавлены институт и оценка) |
| 65 | вид исследовательской работы (ВКР); … название темы выпускной квалификационной работы; … город, год написания. | `title_page_content_default` | покрыто |
| 66 | Во введении следует обосновать выбор темы, ее актуальность, отразить причину исследовательского интереса к … | — | не покрыто: не покрыто: содержание введения проверяется человеком |
| 67 | Анализ эмпирических данных должен обязательно завершаться выводами. | — | не покрыто: не покрыто: содержание выводов проверяется человеком |

## Переименования и слияния относительно набора 3.1.0

| Было | Стало | Причина |
|---|---|---|
| `page_number_position_extended` | `page_number_position` | параметры номера страницы слиты с базовым правилом |
| `heading_format_no_underline` | `heading_format_text` | подчёркивание и регистр — параметры одного правила |
| `heading_page_break_subsection` | `heading_page_break` | запрет новой страницы для подраздела — параметр `forbid_new_page_roles` |
| `bibliography_order_groups` | `bibliography_order` | группы списка — параметр `allow_groups` |
| `caption_format_capitalize` | `caption_format_tables` | заглавная буква — параметр `capitalize_first_letter` |
| `caption_numbering_urgpu` | `caption_numbering_tables` | допустимые схемы — параметр `schemes` |
| `list_format_urgpu` | `list_format_default` | маркеры уровней заданы общим правилом |
| `toc_format_urgpu` | `toc_format_default` | отточие, выравнивание и регистр — параметры одного правила |

Остальные правила 3.1.0 сохранили `rule_id`; поведение базового набора совпадает с 3.0.0, потому что у 37 исходных правил не изменились `code`, `params`, `strictness`, `message` и `target` — заменена только `quote` на дословную формулировку методички.

## Правки после верификации

| `rule_id` | Что изменилось |
|---|---|
| `caption_placement_table` | верификация (t29): база переведена на `alignment: right` — прежнее `left` противоречило собственной цитате и давало 6 ложных находок; «слева с тире» описано как будущее переопределение профиля, а не значение базы |
| `bibliography_format_dot_dash` | верификация (t29): добавлен `exempt_categories` — запрет точки-тире не применяется к электронным ресурсам и нормативным актам, у которых «. — » входит в форму описания |
| `list_format_default` | верификация (t29): абзацы раздела списка литературы исключены из проверки перечислений через `document_tree` и `cross_ref_index`, нумерация записей списка больше не считается нарушением |
| `document_volume` | верификация (t29): находки получили адрес (`section: document`, страница по числу страниц, фрагмент с объёмом) |
| `bibliography_count` | верификация (t29): сводные находки количества и свежести источников привязаны к страницам списка литературы |
| `page_number_position` | верификация (t29): идентификаторы нарушений уникальны — к адресу добавлен ключ требования (`page:1:skip_first_page`) |

## Косвенные основания цитат

| Правило | Почему цитата косвенная |
|---|---|
| `microtypography_prose` | проверяет кавычки и тире оформления, а не дословность цитаты |
| `microtypography_nbsp` | неразрывные пробелы — общая культура набора, отдельного пункта нет |
| `page_format` | формат A4 в методичке не проговорён, взят как общий стандарт ВКР |
| `widow_control_body` | висячие строки прямо не названы, основание — требование о разрывах страниц |
| `heading_levels` | иерархия заголовков следует из требования к нумерации разделов |
| `document_volume` | объём выводится из требования к списку литературы и структуре работы |
| `equation_numbering` | нумерация формул методичкой не описана, включена как общий стандарт |
| `bibliography_links_resolved` | косвенно: ссылки в тексте должны указывать на источники |
| `bibliography_recency` | косвенно: требование о научной новизне источников |
| `alignment_captions` | косвенно: подписи выравниваются по правилу своего объекта |
| `alignment_table_cells` | косвенно: содержимое таблицы подчиняется общим полям |

