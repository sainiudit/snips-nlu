from intent_parser import IntentParser
from snips_nlu.constants import (DATA, INTENTS, SLOT_NAME, UTTERANCES, ENTITY,
                                 CUSTOM_ENGINE)
from snips_nlu.dataset import filter_dataset
from snips_nlu.result import ParsedSlot
from snips_nlu.slot_filler.crf_tagger import CRFTagger
from snips_nlu.slot_filler.crf_utils import (tags_to_slots,
                                             utterance_to_sample)
from snips_nlu.slot_filler.data_augmentation import augment_utterances
from snips_nlu.tokenization import tokenize
from snips_nlu.utils import instance_to_generic_dict, instance_from_dict


def get_slot_name_to_entity_mapping(dataset):
    slot_name_to_entity = dict()
    for intent in dataset[INTENTS].values():
        for utterance in intent[UTTERANCES]:
            for chunk in utterance[DATA]:
                if SLOT_NAME in chunk:
                    slot_name_to_entity[chunk[SLOT_NAME]] = chunk[ENTITY]
    return slot_name_to_entity


class CRFIntentParser(IntentParser):
    def __init__(self, intent_classifier, crf_taggers,
                 slot_name_to_entity_mapping=None, n_augmented_queries=200,
                 noise_prob=0.05, min_noise_size=1, max_noise_size=3):
        super(CRFIntentParser, self).__init__()
        self.intent_classifier = intent_classifier
        self.crf_taggers = crf_taggers
        self.slot_name_to_entity_mapping = slot_name_to_entity_mapping
        self.n_augmented_queries = n_augmented_queries
        self.noise_prob = noise_prob
        self.min_noise_size = min_noise_size
        self.max_noise_size = max_noise_size

    def get_intent(self, text):
        if not self.fitted:
            raise ValueError("CRFIntentParser must be fitted before "
                             "`get_intent` is called")
        return self.intent_classifier.get_intent(text)

    def get_slots(self, text, intent=None):
        if intent is None:
            raise ValueError("intent can't be None")
        if not self.fitted:
            raise ValueError("CRFIntentParser must be fitted before "
                             "`get_slots` is called")
        if intent not in self.crf_taggers:
            raise KeyError("Invalid intent '%s'" % intent)
        tokens = tokenize(text)
        tagger = self.crf_taggers[intent]

        tags = tagger.get_tags(tokens)
        slots = tags_to_slots(tokens, tags,
                              tagging_scheme=tagger.tagging_scheme)
        return [ParsedSlot(match_range=s["range"],
                           value=text[s["range"][0]:s["range"][1]],
                           entity=self.slot_name_to_entity_mapping[
                               s[SLOT_NAME]],
                           slot_name=s[SLOT_NAME]) for s in slots]

    @property
    def fitted(self):
        return self.intent_classifier.fitted and all(
            slot_filler.fitted for slot_filler in self.crf_taggers.values())

    def fit(self, dataset):
        tagger_languages = [tagger.language for tagger
                            in self.crf_taggers.values()]
        language = tagger_languages[0]
        if not all(l == language for l in tagger_languages[1:]):
            raise ValueError("Found taggers with different languages")
        custom_dataset = filter_dataset(dataset, CUSTOM_ENGINE)
        self.slot_name_to_entity_mapping = get_slot_name_to_entity_mapping(
            custom_dataset)
        self.intent_classifier = self.intent_classifier.fit(dataset)
        for intent_name in custom_dataset[INTENTS]:
            augmented_intent_utterances = augment_utterances(
                dataset, intent_name, language=language,
                max_utterances=self.n_augmented_queries,
                noise_prob=self.noise_prob, min_noise_size=self.min_noise_size,
                max_noise_size=self.max_noise_size)
            tagging_scheme = self.crf_taggers[intent_name].tagging_scheme
            crf_samples = [utterance_to_sample(u[DATA], tagging_scheme)
                           for u in augmented_intent_utterances]
            self.crf_taggers[intent_name] = self.crf_taggers[intent_name].fit(
                crf_samples)
        return self

    def to_dict(self):
        obj_dict = instance_to_generic_dict(self)
        obj_dict.update({
            "intent_classifier": self.intent_classifier.to_dict(),
            "crf_taggers": {intent_name: tagger.to_dict() for
                            intent_name, tagger in
                            self.crf_taggers.iteritems()},
            "slot_name_to_entity_mapping": self.slot_name_to_entity_mapping,
            "n_augmented_queries": self.n_augmented_queries,
            "noise_prob": self.noise_prob,
            "min_noise_size": self.min_noise_size,
            "max_noise_size": self.max_noise_size
        })
        return obj_dict

    @classmethod
    def from_dict(cls, obj_dict):
        return cls(
            intent_classifier=instance_from_dict(
                obj_dict["intent_classifier"]),
            crf_taggers={intent_name: CRFTagger.from_dict(tagger_dict)
                         for intent_name, tagger_dict in
                         obj_dict["crf_taggers"].iteritems()},
            slot_name_to_entity_mapping=obj_dict[
                "slot_name_to_entity_mapping"],
            noise_prob=obj_dict["noise_prob"],
            n_augmented_queries=obj_dict["n_augmented_queries"],
            min_noise_size=obj_dict["min_noise_size"],
            max_noise_size=obj_dict["max_noise_size"]
        )
