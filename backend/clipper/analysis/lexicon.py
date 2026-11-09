"""Multilingual (es/en) cue lexicons.

These are *cues*, never sole decision rules: every detector combines them with acoustic, visual,
chat and (optionally) LLM evidence. Phrases are matched on accent-stripped lowercase text.
"""

from __future__ import annotations

HOOK = {
    "curiosity": [
        "no vas a creer", "no te vas a creer", "lo que paso", "nadie sabe", "el secreto", "te voy a contar",
        "sabias que", "lo que nadie", "adivina", "mira esto", "esto es lo que", "lo mas loco", "jamas imagine",
        "you won't believe", "you wont believe", "here's the thing", "heres the thing", "nobody talks about",
        "the secret", "let me tell you", "did you know", "guess what", "watch this", "the craziest", "what nobody",
        "this is why", "here's why", "heres why", "wait for it",
    ],
    "surprise": [
        "no puede ser", "en serio", "que dices", "no manches", "no mames", "dios mio", "increible", "que fuerte",
        "madre mia", "que locura", "no way", "oh my god", "oh my gosh", "what the", "are you serious", "holy",
        "wow", "no lo puedo creer", "i can't believe", "i cant believe", "unbelievable", "insane",
    ],
    "conflict": [
        "no estoy de acuerdo", "estas equivocado", "eso es mentira", "mentira", "no tienes razon", "callate",
        "basta", "no me hables", "que te pasa", "i disagree", "you're wrong", "youre wrong", "that's not true",
        "thats not true", "that's a lie", "shut up", "how dare you", "excuse me", "are you kidding",
    ],
    "revelation": [
        "la verdad es que", "confieso", "nunca le he dicho", "nunca se lo he contado", "voy a ser sincero",
        "voy a ser honesto", "admito", "lo reconozco", "por primera vez", "the truth is", "i confess",
        "i've never told", "ive never told", "to be honest", "honestly", "i admit", "for the first time",
        "i have to tell you", "tengo que decirles",
    ],
    "controversy": [
        "opinion impopular", "polemico", "lo odio", "sobrevalorado", "el peor", "la peor", "no soporto",
        "me da asco", "unpopular opinion", "controversial", "i hate", "overrated", "the worst", "can't stand",
        "cant stand", "trash", "basura",
    ],
    "tension": [
        "tengo miedo", "que nervios", "cuidado", "corre", "ayuda", "i'm scared", "im scared", "careful", "run",
        "help", "oh no", "no no no", "please please",
    ],
}

QUESTION_STARTERS = [
    "por que", "como", "que pasaria si", "quien", "cual", "cuando", "donde", "alguna vez", "why", "how",
    "what if", "who", "which", "when", "where", "have you ever", "would you", "do you think", "que opinas",
]

HUMOR = [
    "jaja", "jajaja", "jajajaja", "jeje", "xd", "lol", "lmao", "haha", "hahaha", "me muero", "me meo",
    "que risa", "i'm dying", "im dying", "i'm crying", "im crying", "hilarious", "chiste", "joke", "broma",
    "prank", "ridiculo", "absurdo", "ridiculous", "absurd", "que tonto", "so dumb",
]

STORY = {
    "intro": [
        "una vez", "el otro dia", "te cuento", "les cuento", "resulta que", "cuando era", "hace años", "hace anos",
        "hace tiempo", "me paso", "os cuento", "so one time", "one time", "the other day", "back when", "story time",
        "when i was", "years ago", "so basically", "this one time", "let me tell you", "what happened was",
    ],
    "development": [
        "entonces", "y luego", "despues", "luego", "mientras", "and then", "so then", "after that", "next thing",
        "meanwhile", "later",
    ],
    "turn": [
        "pero", "de repente", "de la nada", "y de pronto", "sin embargo", "but then", "suddenly", "out of nowhere",
        "all of a sudden", "turns out", "resulto que", "y ahi", "and that's when", "and thats when",
    ],
    "resolution": [
        "al final", "total que", "y asi fue", "en fin", "y ya", "moraleja", "por eso", "in the end",
        "so yeah", "long story short", "moral of the story", "anyway", "and that's how", "and thats how",
        "that's why", "thats why", "finally", "finalmente",
    ],
}

VALUE = [
    "el truco", "consejo", "la clave", "paso", "primero", "segundo", "lo importante", "recuerda", "dato",
    "sabias que", "aprende", "se hace", "funciona", "el error", "tip", "the trick", "the key", "step",
    "first", "second", "how to", "important", "remember", "fact", "learn", "the mistake", "here's how",
    "heres how", "the reason", "la razon", "por eso", "explicacion", "means", "significa",
]

OPINION = [
    "creo que", "pienso que", "para mi", "en mi opinion", "opino", "la verdad", "i think", "i believe",
    "in my opinion", "personally", "i feel like", "to me",
]

STRONG = [
    "nunca", "siempre", "jamas", "el mejor", "la mejor", "never", "always", "the best", "best ever",
    "worst ever", "everyone", "todo el mundo", "nadie", "nobody", "literally", "literalmente",
]

EXCLAMATIONS = [
    "wow", "dios", "madre", "ostia", "hostia", "joder", "wey", "guau", "omg", "damn", "dang", "bro", "dude",
    "what", "que", "vamos", "let's go", "lets go", "yes", "no",
]

GAMING_EVENTS = [
    "gg", "clutch", "ace", "headshot", "kill", "lo mate", "victoria", "victory", "win", "gane", "perdi",
    "died", "me mataron", "boss", "jefe", "level up", "nivel", "loot", "ranked", "one tap", "triple",
    "quadra", "penta", "traicion", "betrayal", "jumpscare", "susto", "fail", "noob",
]

FILLERS = ["eh", "em", "este", "o sea", "pues", "bueno", "um", "uh", "uhm", "like", "you know", "i mean", "kinda"]

# words that make a sentence depend on previous context when they open it
ANAPHORA_STARTERS = [
    "y", "pero", "eso", "esto", "esa", "ese", "el", "ella", "ellos", "entonces", "porque", "ademas", "tambien",
    "and", "but", "so", "that", "this", "it", "he", "she", "they", "because", "also", "which", "then",
]

CATEGORY_TERMS = {
    "podcast": ["podcast", "episodio", "invitado", "entrevista", "bienvenido al", "episode", "guest", "interview",
                "welcome to the show", "welcome back", "the show", "bienvenidos", "nuestro invitado", "conversacion", "conversation", "pregunta", "question"],
    "debate": ["debate", "argumento", "de acuerdo", "discrepo", "opinion", "argue", "agree", "disagree",
               "point", "punto", "postura", "position", "politica", "politics"],
    "gaming": ["game", "juego", "partida", "nivel", "boss", "kill", "ranked", "match", "server", "loot", "respawn",
               "mapa", "map", "skin", "build", "team", "squad", "lobby", "mision", "quest", "fortnite", "minecraft"],
    "education": ["explicar", "aprender", "leccion", "ejemplo", "formula", "concepto", "learn", "lesson", "example",
                  "explain", "tutorial", "step", "paso", "clase", "class", "teoria", "theory", "ejercicio"],
    "news": ["noticia", "gobierno", "presidente", "ultima hora", "breaking", "report", "government", "election",
             "elecciones", "ministro", "minister", "oficial", "official"],
    "sports": ["gol", "partido", "equipo", "jugador", "goal", "team", "player", "score", "liga", "league",
               "arbitro", "referee", "penalti", "penalty", "cancha", "field"],
    "cooking": ["receta", "cocinar", "ingredientes", "horno", "sal", "recipe", "cook", "ingredients", "oven",
                "bake", "sarten", "pan", "minutos", "sabor", "flavor", "taste"],
    "music": ["cancion", "cantar", "musica", "beat", "song", "sing", "album", "guitarra", "guitar", "piano",
              "letra", "lyrics", "nota", "chord"],
    "beauty": ["maquillaje", "piel", "makeup", "skin", "labial", "lipstick", "base", "foundation", "pelo", "hair"],
    "tech": ["codigo", "programa", "app", "software", "computadora", "code", "ai", "ia", "phone", "chip", "server",
             "python", "javascript", "gpu", "tecnologia", "technology"],
    "irl": ["calle", "gente", "ciudad", "caminar", "street", "people", "city", "walking", "hotel", "restaurante",
            "restaurant", "aeropuerto", "airport", "viaje", "trip", "tienda", "store"],
    "entertainment": ["reto", "challenge", "broma", "prank", "reaccion", "reaction", "jaja", "haha", "show",
                      "chat", "donacion", "donation", "sub", "suscripcion"],
    "motivational": ["sueño", "sueno", "metas", "disciplina", "exito", "dream", "goals", "discipline", "success",
                     "mindset", "nunca te rindas", "never give up", "esfuerzo"],
}

# platform category names / YouTube categoryId -> internal category
PLATFORM_CATEGORY_MAP = {
    "just chatting": "entertainment", "talk shows & podcasts": "podcast", "irl": "irl", "music": "music",
    "sports": "sports", "food & drink": "cooking", "science & technology": "tech",
    "software and game development": "tech", "beauty & body art": "beauty", "politics": "debate",
    "travel & outdoors": "irl", "pools, hot tubs, and beaches": "irl", "asmr": "entertainment",
    "special events": "entertainment", "makers & crafting": "education", "art": "education",
    "fitness & health": "motivational", "chess": "gaming",
    # youtube categoryId
    "20": "gaming", "10": "music", "17": "sports", "25": "news", "27": "education", "28": "tech",
    "22": "irl", "24": "entertainment", "23": "entertainment", "26": "education", "19": "irl",
}

CATEGORIES = ["entertainment", "podcast", "debate", "irl", "gaming", "education", "news", "sports",
              "cooking", "music", "beauty", "tech", "motivational"]

# chat emotes / tokens -> collective reaction
CHAT_EMOTIONS = {
    "laugh": ["kekw", "lul", "lulw", "omegalul", "icant", "kek", "jaja", "jajaja", "jajajaja", "xd", "xdd", "lol",
              "lmao", "lmfao", "haha", "hahaha", "😂", "🤣", "💀", "pepelaugh", "kekwait"],
    "hype": ["pog", "pogchamp", "poggers", "pogu", "w", "ww", "www", "lets go", "letsgo", "vamos", "🔥", "goat",
             "clip it", "clipit", "clip", "clipeen", "clipea", "ez", "gg"],
    "surprise": ["?", "??", "???", "what", "que", "wtf", "omg", "wait", "no way", "😮", "😳", "😲", "wut", "huh"],
    "scare": ["monkas", "monkaw", "😱", "😨", "scary", "miedo"],
    "sad": ["f", "sadge", "😢", "😭", "rip", "pepehands", "biblethump"],
    "negative": ["l", "ll", "ratio", "cringe", "🤮", "boring", "aburrido", "ban"],
    "shock": ["d:", "wutface", "dansgame", "notlikethis", "🤯"],
    "love": ["<3", "❤", "❤️", "😍", "love", "te amo"],
}
