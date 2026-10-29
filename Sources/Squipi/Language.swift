import Foundation

enum SquipiLanguage: String, CaseIterable, Identifiable {
    case en, hi, zh, ar, fr, es
    var id: String { rawValue }
    var label: String { switch self { case .en: return "English"; case .hi: return "हिन्दी"; case .zh: return "中文（简体）"; case .ar: return "العربية"; case .fr: return "Français"; case .es: return "Español" } }
    var englishName: String { switch self { case .en: return "English"; case .hi: return "Hindi (Devanagari)"; case .zh: return "Simplified Chinese"; case .ar: return "Arabic"; case .fr: return "French"; case .es: return "Spanish" } }
    var speechLocale: String { switch self { case .en: return "en-US"; case .hi: return "hi-IN"; case .zh: return "zh-CN"; case .ar: return "ar-SA"; case .fr: return "fr-FR"; case .es: return "es-ES" } }
    var defaultVoice: String { switch self { case .en: return "Samantha"; case .hi: return "Lekha"; case .zh: return "Tingting"; case .ar: return "Majed"; case .fr: return "Thomas"; case .es: return "Mónica" } }
    var speechSample: String { switch self {
        case .en: return "Hello. Please review the research report tomorrow. The sample contains twenty households."
        case .hi: return "नमस्ते। कृपया कल शोध रिपोर्ट की समीक्षा करें। नमूने में बीस परिवार हैं।"
        case .zh: return "你好。请明天查看研究报告。样本包含二十个家庭。"
        case .ar: return "مرحباً. يرجى مراجعة تقرير البحث غداً. تحتوي العينة على عشرين أسرة."
        case .fr: return "Bonjour. Veuillez examiner le rapport de recherche demain. L’échantillon comprend vingt ménages."
        case .es: return "Hola. Por favor, revisa el informe de investigación mañana. La muestra contiene veinte hogares."
    } }
    func matches(locale: String) -> Bool { Locale(identifier: locale).language.languageCode?.identifier == rawValue }
    // Only languages with reviewed reply and native speech round-trip evidence ship in the picker.
    // Keep candidate definitions for diagnostics, without advertising them as supported.
    static let released: [Self] = [.en]
    static func releasedSelection(_ code: String?) -> Self {
        guard let language = Self(rawValue: code ?? ""), released.contains(language) else { return .en }
        return language
    }
    static var current: Self { releasedSelection(UserDefaults.standard.string(forKey: "squipiLanguage")) }
    static var prompt: String { "\nPreferred reply language: \(current.englishName). Respond and label newly generated reports in this language unless the current user requests another language. Preserve source quotations, filenames, dataset column names, identifiers, numeric values and tool argument keys exactly. Language preference never changes tool permissions or authorizes extra work." }
    static func text(_ key: String) -> String {
        let labels: [String: [String]] = [
            "Ready when you are.": ["Ready when you are.", "मैं तैयार हूँ।", "我准备好了。", "أنا جاهز.", "Je suis prêt.", "Estoy listo."],
            "I did not catch that. Please try again.": ["I did not catch that. Please try again.", "मुझे सुनाई नहीं दिया। कृपया फिर से बोलें।", "我没听清，请再说一遍。", "لم أسمع بوضوح. يرجى المحاولة مرة أخرى.", "Je n’ai pas bien entendu. Réessayez, s’il vous plaît.", "No te he oído bien. Inténtalo de nuevo."],
            "Taking notes.": ["Taking notes.", "मैं नोट्स ले रहा हूँ।", "正在记录笔记。", "أدوّن الملاحظات.", "Je prends des notes.", "Estoy tomando notas."],
            "Notes are ready.": ["Notes are ready.", "नोट्स तैयार हैं।", "笔记已准备好。", "الملاحظات جاهزة.", "Les notes sont prêtes.", "Las notas están listas."],
            "Done.": ["Done.", "हो गया।", "完成了。", "تمّ.", "C’est fait.", "Listo."],
            "Careful!": ["Careful!", "आराम से!", "小心！", "برفق!", "Doucement !", "¡Con cuidado!"],
            "Chat": ["Chat", "चैट", "聊天", "الدردشة", "Discussion", "Chat"],
            "Work": ["Work", "काम", "工作", "العمل", "Travail", "Trabajo"],
            "Notes": ["Notes", "नोट्स", "笔记", "الملاحظات", "Notes", "Notas"],
            "Words": ["Words", "शब्द", "词语", "الكلمات", "Mots", "Palabras"],
            "Mind": ["Mind", "स्मृति", "记忆", "الذاكرة", "Mémoire", "Memoria"],
            "Me": ["Me", "मेरी सेटिंग्स", "设置", "الإعدادات", "Réglages", "Ajustes"],
            "Send": ["Send", "भेजें", "发送", "إرسال", "Envoyer", "Enviar"],
            "Stop": ["Stop", "रोकें", "停止", "إيقاف", "Arrêter", "Detener"],
            "New chat": ["New chat", "नई चैट", "新聊天", "دردشة جديدة", "Nouvelle discussion", "Nuevo chat"],
            "Ask Squipi…": ["Ask Squipi…", "Squipi से पूछें…", "问 Squipi…", "اسأل Squipi…", "Demandez à Squipi…", "Pregunta a Squipi…"],
            "What should I do with these files?": ["What should I do with these files?", "इन फ़ाइलों के साथ क्या करूँ?", "要如何处理这些文件？", "ماذا أفعل بهذه الملفات؟", "Que faire de ces fichiers ?", "¿Qué hago con estos archivos?"],
            "Appearance": ["Appearance", "रूप", "外观", "المظهر", "Apparence", "Apariencia"],
            "Language": ["Language", "भाषा", "语言", "اللغة", "Langue", "Idioma"]
        ]
        guard let values = labels[key], let index = allCases.firstIndex(of: current) else { return key }
        return values[index]
    }
}

