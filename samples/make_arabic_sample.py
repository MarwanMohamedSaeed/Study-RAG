"""Build samples/arabic_lecture.pdf: a short lecture written entirely in Arabic (data structures).

Used to test Arabic-only documents end to end (extraction, chunking, search, answers).
Run:  python samples/make_arabic_sample.py
"""
from pathlib import Path

import pymupdf as fitz

OUT = Path(__file__).with_name("arabic_lecture.pdf")
ARIAL = Path("C:/Windows/Fonts/arial.ttf")

PAGES = [
    ("المحاضرة الثالثة: هياكل البيانات",
     "مقرر: هياكل البيانات والخوارزميات.<br/><br/>"
     "تتناول هذه المحاضرة أهم هياكل البيانات الخطية وغير الخطية: المصفوفة، والقائمة المترابطة، والمكدس، "
     "والطابور، وجدول التجزئة، والشجرة الثنائية. الهدف هو أن يعرف الطالب متى يستخدم كل هيكل، وما تكلفة "
     "العمليات الأساسية عليه من حيث الزمن."),
    ("١. المصفوفة والقائمة المترابطة",
     "المصفوفة تخزن العناصر في مواقع متجاورة في الذاكرة، لذلك يمكن الوصول إلى أي عنصر مباشرة عن طريق "
     "الفهرس في زمن ثابت O(1). لكن إدراج عنصر في منتصف المصفوفة يتطلب إزاحة العناصر التالية، أي زمن O(n).<br/><br/>"
     "القائمة المترابطة تتكون من عقد، وكل عقدة تحتوي على قيمة ومؤشر إلى العقدة التالية. الإدراج والحذف بعد "
     "عقدة معروفة يتم في زمن O(1)، لكن الوصول إلى العنصر رقم k يتطلب المرور على العقد واحدة تلو الأخرى، أي زمن O(n)."),
    ("٢. المكدس والطابور",
     "المكدس هيكل بيانات يعمل بمبدأ آخر داخل أول خارج (LIFO): آخر عنصر يُضاف هو أول عنصر يُحذف. "
     "العمليتان الأساسيتان هما الدفع (push) والسحب (pop). يُستخدم المكدس في تنفيذ استدعاءات الدوال، "
     "وفي التراجع عن العمليات في برامج تحرير النصوص.<br/><br/>"
     "الطابور يعمل بمبدأ أول داخل أول خارج (FIFO): العنصر الذي يدخل أولاً يخرج أولاً. يُستخدم الطابور "
     "في جدولة المهام في نظام التشغيل وفي البحث بالعرض أولاً (BFS) في الرسوم البيانية."),
    ("٣. جدول التجزئة والشجرة الثنائية",
     "جدول التجزئة يحوّل المفتاح إلى موقع في مصفوفة باستخدام دالة تجزئة، فيصبح البحث والإدراج في زمن "
     "O(1) في المتوسط. عندما يقع مفتاحان في الموقع نفسه يحدث تصادم، ويُعالج بالسلاسل أو بالعنونة المفتوحة.<br/><br/>"
     "شجرة البحث الثنائية تحتفظ في كل عقدة بقيم أصغر في الفرع الأيسر وقيم أكبر في الفرع الأيمن. "
     "إذا كانت الشجرة متوازنة فإن البحث يتم في زمن O(log n)، أما إذا أصبحت مائلة مثل القائمة فإن "
     "البحث يصبح O(n). لذلك تُستخدم أشجار متوازنة مثل أشجار AVL."),
]


def build(out: Path = OUT) -> Path:
    doc = fitz.open()
    rect = fitz.Rect(56, 56, 539, 786)
    archive, css = None, "* {font-size: 13pt;}"
    if ARIAL.exists():
        archive = fitz.Archive(str(ARIAL.parent))
        css = "@font-face {font-family: ar; src: url(arial.ttf);} * {font-family: ar; font-size: 13pt;}"
    for title, body in PAGES:
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(rect, f'<div dir="rtl"><h2>{title}</h2><p>{body}</p></div>', css=css, archive=archive)
    doc.subset_fonts()
    doc.save(out, garbage=4, deflate=True)
    return out


if __name__ == "__main__":
    print("wrote", build())
