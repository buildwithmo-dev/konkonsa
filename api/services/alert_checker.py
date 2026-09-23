from datetime import datetime, timedelta
from sqlalchemy import select, func

from database import AsyncSessionLocal
from models import Alert, AlertHistory, AlertStatus, FeedItem, Classification, Trend


async def check_all_alerts() -> dict:
    """
    Evaluate all active alerts and fire those whose conditions are met.
    Returns {"triggered": N, "checked": M}
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Alert).where(Alert.status == AlertStatus.active)
        )
        alerts = result.scalars().all()

    triggered = 0
    for alert in alerts:
        fired = await evaluate_alert(alert)
        if fired:
            triggered += 1

    return {"triggered": triggered, "checked": len(alerts)}


async def evaluate_alert(alert: Alert) -> bool:
    """
    Evaluate a single alert's condition.
    Condition schema examples:
      {"type": "keyword_spike", "value": "AI", "threshold": 10, "window_hours": 1}
      {"type": "new_pain_points", "threshold": 5, "window_hours": 6}
      {"type": "trend_score", "min_score": 8.0}
      {"type": "sentiment_drop", "threshold": -0.5, "window_hours": 24}
    """
    condition = alert.condition or {}
    alert_type = condition.get("type")
    fired = False
    payload = {}

    async with AsyncSessionLocal() as db:

        if alert_type == "keyword_spike":
            keyword = condition.get("value", "")
            threshold = condition.get("threshold", 10)
            hours = condition.get("window_hours", 1)
            since = datetime.utcnow() - timedelta(hours=hours)

            count = (await db.execute(
                select(func.count(FeedItem.id))
                .where(FeedItem.fetched_at >= since)
                .where(FeedItem.title.ilike(f"%{keyword}%"))
            )).scalar_one()

            if count >= threshold:
                fired = True
                payload = {"keyword": keyword, "count": count, "window_hours": hours}

        elif alert_type == "new_pain_points":
            threshold = condition.get("threshold", 5)
            hours = condition.get("window_hours", 6)
            since = datetime.utcnow() - timedelta(hours=hours)
            from models import ItemType

            count = (await db.execute(
                select(func.count(Classification.id))
                .where(Classification.item_type == ItemType.pain_point)
                .where(Classification.classified_at >= since)
            )).scalar_one()

            if count >= threshold:
                fired = True
                payload = {"pain_point_count": count, "window_hours": hours}

        elif alert_type == "trend_score":
            min_score = condition.get("min_score", 8.0)

            high_score = (await db.execute(
                select(Trend)
                .where(Trend.score >= min_score)
                .order_by(Trend.score.desc())
                .limit(5)
            )).scalars().all()

            if high_score:
                fired = True
                payload = {
                    "top_trends": [{"id": t.id, "title": t.title, "score": t.score} for t in high_score]
                }

        elif alert_type == "rising_trend":
            count = (await db.execute(
                select(func.count(Trend.id)).where(Trend.is_rising == True)
            )).scalar_one()

            threshold = condition.get("threshold", 1)
            if count >= threshold:
                fired = True
                payload = {"rising_trend_count": count}

        if fired:
            # Record history
            history = AlertHistory(alert_id=alert.id, payload=payload)
            db.add(history)

            # Update alert metadata
            result = await db.execute(select(Alert).where(Alert.id == alert.id))
            alert_obj = result.scalar_one_or_none()
            if alert_obj:
                alert_obj.last_triggered_at = datetime.utcnow()
                alert_obj.trigger_count += 1
                alert_obj.status = AlertStatus.triggered

            await db.commit()

            # Dispatch notification
            await notify(alert, payload)

    return fired


async def notify(alert: Alert, payload: dict):
    """
    Send a notification based on the alert's notification_channel.
    Channels: log | webhook | email (email requires additional setup)
    """
    channel = alert.notification_channel
    config = alert.notification_config or {}

    if channel == "log":
        import logging
        logging.getLogger("alerts").warning(
            f"[ALERT TRIGGERED] {alert.name} | payload={payload}"
        )

    elif channel == "webhook":
        import httpx
        url = config.get("url")
        if url:
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    await client.post(url, json={
                        "alert_name": alert.name,
                        "triggered_at": datetime.utcnow().isoformat(),
                        "payload": payload,
                    })
                except Exception as e:
                    import logging
                    logging.getLogger("alerts").error(f"Webhook failed for {alert.name}: {e}")

    elif channel == "email":
        # Requires smtplib or a service like SendGrid/Resend
        # Stub for now
        import logging
        logging.getLogger("alerts").info(
            f"[EMAIL stub] Would send to {config.get('to')}: {alert.name}"
        )
