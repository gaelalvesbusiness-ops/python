# Request to the HonestPool operator / Запрос администрации HonestPool

**Status: draft, not sent.** The client should review it, fill in the placeholders and send it
through the operator's published contact (Telegram @homlol_official, listed in the official
repository github.com/homlol/honestpool as the contact for questions and offers).

---

## Русская версия

**Тема:** Авторизованный доступ к списку проверенных сегментов пазла 71

Здравствуйте!

Меня зовут [ИМЯ], я представляю [КОМПАНИЯ / ПРОЕКТ]. Мы анализируем ход проверки пазла 71 на
honestpool.ru и хотели бы получать список проверенных сегментов официальным и согласованным
с вами способом, не создавая лишней нагрузки на сайт.

Сейчас эти данные доступны только через постраничный просмотр списка (671 089 страниц), и
каждая сессия требует прохождения CAPTCHA. Обходить CAPTCHA или ограничения сайта мы не
собираемся. Поэтому просим вас рассмотреть следующие вопросы:

1. **Экспорт.** Можете ли вы предоставить авторизованную выгрузку номеров проверенных
   сегментов пазла 71 (например, CSV или JSON)? Подойдёт разовая выгрузка или периодически
   обновляемый файл.
2. **Документированный эндпоинт.** Есть ли или планируется ли документированный API-метод,
   возвращающий идентификаторы (номера) проверенных сегментов, например с постраничной
   выдачей или с фильтром по дате проверки?
3. **Автоматизированный доступ.** Разрешаете ли вы автоматизированный сбор этих данных без
   участия человека? Если да, какой способ аутентификации вы поддерживаете (API-ключ, токен,
   регистрация клиента или другой)?
4. **Частота запросов.** Какая частота запросов допустима и какие действуют лимиты? Есть ли
   предпочтительное время суток для такой нагрузки?
5. **Условия использования.** Есть ли условия, которые нам нужно соблюдать: указание
   источника, запрет на распространение, ограничения по целям использования и т. п.?

Полные шестнадцатеричные значения нам не нужны. Начало и конец каждого сегмента однозначно
вычисляются по его номеру (начало = 0x400000000000000000 + (номер − 1) × 2^44, размер
сегмента 2^44). Поэтому списка номеров проверенных сегментов будет достаточно.

Будем рады любому варианту, который вам удобен, и готовы соблюдать ваши правила и лимиты.
Заранее спасибо!

С уважением,
[ИМЯ]
[КОМПАНИЯ / ПРОЕКТ]
[КОНТАКТЫ]

---

## English version

**Subject:** Authorized access to the verified segment list for puzzle 71

Hello,

My name is [NAME], and I represent [COMPANY / PROJECT]. We are analysing verification progress
for puzzle 71 on honestpool.ru. We would like to obtain the list of verified segments through an
official channel that you approve, without putting unnecessary load on the website.

At present this information is only available by browsing the listing page by page (671,089
pages), and each session requires a CAPTCHA. We do not intend to bypass the CAPTCHA or any other
restriction of the site. We would therefore like to ask:

1. **Export.** Could you provide an authorized export of the verified segment numbers for
   puzzle 71 (for example CSV or JSON)? Either a one-time file or a periodically updated one
   would work.
2. **Documented endpoint.** Is there, or is there planned, a documented API endpoint that returns
   the identifiers (numbers) of verified segments, for example paginated or filtered by
   verification date?
3. **Unattended collection.** Do you permit automated collection of this data without a person
   present? If so, which form of authentication do you support (API key, token, client
   registration or another mechanism)?
4. **Request frequency.** What request frequency is permitted and which limits apply? Is there a
   preferred time of day for this kind of traffic?
5. **Usage conditions.** Are there conditions we should follow, such as attribution,
   no redistribution, or restrictions on purpose?

We do not need the full hexadecimal values. The start and end of every segment follow directly
from its number (start = 0x400000000000000000 + (number − 1) × 2^44, with a segment size of
2^44). A list of verified segment numbers alone would therefore be sufficient.

We are happy to work with whichever option suits you, and we will follow your rules and limits.
Thank you in advance.

Kind regards,
[NAME]
[COMPANY / PROJECT]
[CONTACT DETAILS]
