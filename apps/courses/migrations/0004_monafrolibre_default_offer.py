from django.db import migrations, models
import django.db.models.deletion


OFFER = {
    "slug": "mon-afro-libre-tropical",
    "is_default": True,
    "title": "Cheveux crépus longs et libres",
    "subtitle": "La méthode ultime en milieu tropical. Apprenez à construire une routine adaptée à vos cheveux, à votre climat et à votre quotidien.",
    "language": "fr",
    "mockup_image": "images/checkout/monafrolibre-tropical-course.jpg",
    "mockup_alt": "Présentation numérique de la formation Mon Afro Libre : Cheveux crépus longs et libres, sur écran et tablette.",
    "highlights": "Aucun prérequis\n1 h par jour maximum\nÀ votre rythme",
    "closing_note": "Pas besoin de tout savoir pour commencer. Reproduisez les gestes montrés en vidéo, une étape à la fois, et gagnez progressivement en autonomie.",
}

BENEFITS = [
    ("Plus de 10 heures de formation capillaire", "Des vidéos pas à pas pour comprendre la pousse, découvrir le démêlage aux doigts et adapter vos soins au climat tropical.", False),
    ("Les secrets d’une transformation capillaire", "Apprenez à travailler les trois piliers de votre routine : la souplesse, la robustesse du cheveu et le maintien des coiffures face à l’humidité.", False),
    ("Des ressources réservées aux membres", "Retrouvez les outils et ressources complémentaires du programme pour vous accompagner tout au long de votre apprentissage.", False),
    ("Un accompagnement VIP, 7 jours sur 7", "Posez vos questions au support VIP et recevez une réponse en moins de 24 heures, tous les jours de la semaine.", False),
    ("Un accès à vie, les mises à jour incluses", "Avancez à votre rythme, revisionnez les leçons et retrouvez toutes les futures mises à jour dans votre espace membre, sans supplément.", False),
    ("Votre formation aux soins naturels offerte", "Découvrez des recettes simples et naturelles, adaptées aux cheveux crépus en climat tropical humide, chaud ou tempéré.", True),
]


def create_default_offer(apps, schema_editor):
    Offer = apps.get_model("courses", "CheckoutOffer")
    Benefit = apps.get_model("courses", "CheckoutBenefit")
    offer, created = Offer.objects.get_or_create(slug=OFFER["slug"], defaults={k: v for k, v in OFFER.items() if k != "slug"})
    if created:
        Benefit.objects.bulk_create([
            Benefit(offer=offer, title=title, description=description, is_bonus=bonus, display_order=index)
            for index, (title, description, bonus) in enumerate(BENEFITS)
        ])


class Migration(migrations.Migration):
    dependencies = [("courses", "0003_checkoutoffer_checkoutbenefit")]
    # Preserve edited offer content if this data migration alone is reversed.
    operations = [
        migrations.AddField(
            model_name="checkoutoffer", name="is_default",
            field=models.BooleanField(default=False, help_text="Used by every course without its own offer profile."),
        ),
        migrations.AlterField(
            model_name="checkoutoffer", name="course",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="checkout_offer", to="courses.course",
                help_text="Attach an override to a course, or leave blank for the shared default template."),
        ),
        migrations.AddConstraint(
            model_name="checkoutoffer",
            constraint=models.UniqueConstraint(fields=["is_default"], condition=models.Q(is_default=True), name="one_default_checkout_offer"),
        ),
        migrations.RunPython(create_default_offer, migrations.RunPython.noop),
    ]
