# CareBridge

A small web app for people who want help sticking to medication times, and for carers who want a simple window when they are not there in person. Older adults who forget whether they took a dose, carers who worry on work days, and (in the design) GPs who only see patients now and then.


## Features

**Schedules** - Add medications with name, dose, and a time of day. You can edit, turn them off, or delete them.

**Reminders** - After the scheduled time, the app creates a reminder. You get notifications and an email. There is a follow-up prompt if you do not respond to the first one, and doses can end up marked missed if nothing is logged in time.

**Task Marking** - On the reminder screen you can mark **Taken**, **Skipped**, or **Remind me later** (30, 60, or 120 minutes). Skipped only counts once the reminder has actually been issued.

**Carer** - Each user gets a **carer code**. Someone with that code can open the carer portal without a full login and see today’s status, weekly medication and exercise summaries, and recent notices. If **two doses in a row** are missed, and a carer email is set in account settings, the app tries to email the carer.

**Weekly summary** - Taken vs missed counts, adherence percentage, and activity over about the last seven days. You can download a plain-text report or email a summary to a GP address if you saved one.

**Exercise** - Log that you completed today’s exercise once per day. A weekly summary counts how many days you logged in the last stretch.

There is also a **`console_app.py`** , as per task requirements. The app does not need it but we decided to leave it here from sprint 1.

## Requirements

- Python 3.x  
- Dependencies in `requirements.txt` 

## How to run

```text
pip install -r requirements.txt
python main.py
```

### Email 

By default the app assumes a local mail sink on **127.0.0.1:8025** . If nothing is listening, reminder and alert code falls back to printing messages in the terminal so you still see what would have been sent.




