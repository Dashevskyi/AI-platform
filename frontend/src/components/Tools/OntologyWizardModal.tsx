import { useMemo, useState } from 'react';
import {
  Button,
  Group,
  Modal,
  Select,
  SimpleGrid,
  Stack,
  Stepper,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import type { OntologyJson, OntologySection, Tool } from '../../shared/api/types';
import {
  ONTOLOGY_TEMPLATES,
  appendSections,
  buildGlossarySection,
  glossaryFromTools,
  mergeGlossaryItems,
  templateSections,
  type OntologyTemplateId,
} from './ontologyImport';

type Props = {
  opened: boolean;
  onClose: () => void;
  tools: Tool[];
  ontology: OntologyJson | null;
  onApply: (next: OntologyJson) => void;
};

const uid = () => `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;

export function OntologyWizardModal({ opened, onClose, tools, ontology, onApply }: Props) {
  const [step, setStep] = useState(0);
  const [templateId, setTemplateId] = useState<OntologyTemplateId>('blank');
  const [glossaryLines, setGlossaryLines] = useState('');
  const [importTools, setImportTools] = useState(true);
  const [examples, setExamples] = useState([
    { query: '', expected_tool: '' },
    { query: '', expected_tool: '' },
    { query: '', expected_tool: '' },
  ]);
  const toolOptions = useMemo(
    () => tools.filter((t) => t.is_active !== false).map((t) => t.name).sort(),
    [tools],
  );

  const reset = () => {
    setStep(0);
    setTemplateId('blank');
    setGlossaryLines('');
    setImportTools(true);
    setExamples([
      { query: '', expected_tool: '' },
      { query: '', expected_tool: '' },
      { query: '', expected_tool: '' },
    ]);
  };

  const finish = () => {
    let next = ontology ? { ...ontology, sections: [...ontology.sections] } : { version: 1, sections: [] as OntologySection[] };
    const templateSectionsList = templateSections(templateId);
    if (templateSectionsList.length) {
      next = appendSections(next, templateSectionsList);
    }

    const manualItems = glossaryLines
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean)
      .map((line) => {
        const [term, ...rest] = line.split(/[\t;,|]/).map((p) => p.trim());
        return { term: term || '', definition: rest.join(' — ') };
      })
      .filter((i) => i.term);

    const toolItems = importTools ? glossaryFromTools(tools) : [];
    const allGlossary = mergeGlossaryItems([], [...manualItems, ...toolItems]);

    if (allGlossary.length) {
      const existingGlossary = next.sections.find((s) => s.type === 'glossary') as Extract<OntologySection, { type: 'glossary' }> | undefined;
      if (existingGlossary) {
        next = {
          ...next,
          sections: next.sections.map((s) =>
            s === existingGlossary
              ? { ...existingGlossary, items: mergeGlossaryItems(existingGlossary.items, allGlossary) }
              : s,
          ),
        };
      } else {
        next = appendSections(next, [buildGlossarySection('Глоссарий', allGlossary)]);
      }
    }

    const exItems = examples.filter((e) => e.query.trim()).map((e) => ({
      query: e.query.trim(),
      expected_tool: e.expected_tool || '',
      note: '',
    }));
    if (exItems.length) {
      const existingEx = next.sections.find((s) => s.type === 'examples') as Extract<OntologySection, { type: 'examples' }> | undefined;
      if (existingEx) {
        next = {
          ...next,
          sections: next.sections.map((s) =>
            s === existingEx ? { ...existingEx, items: [...existingEx.items, ...exItems] } : s,
          ),
        };
      } else {
        next = appendSections(next, [{ id: uid(), type: 'examples', title: 'Примеры запросов', items: exItems }]);
      }
    }

    onApply(next);
    reset();
    onClose();
  };

  return (
    <Modal
      opened={opened}
      onClose={() => { reset(); onClose(); }}
      title="Мастер онтологии"
      size="lg"
    >
      <Stepper active={step} onStepClick={setStep} allowNextStepsSelect={false} mb="lg">
        <Stepper.Step label="Шаблон" description="Домен">
          <Stack gap="md" mt="md">
            <Text size="sm" c="dimmed">Выберите отраслевой шаблон или начните с пустой онтологии.</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <Button
                variant={templateId === 'blank' ? 'filled' : 'light'}
                onClick={() => setTemplateId('blank')}
              >
                С нуля
              </Button>
              {(Object.entries(ONTOLOGY_TEMPLATES) as [Exclude<OntologyTemplateId, 'blank'>, typeof ONTOLOGY_TEMPLATES.isp][]).map(([id, t]) => (
                <Button
                  key={id}
                  variant={templateId === id ? 'filled' : 'light'}
                  onClick={() => setTemplateId(id)}
                >
                  {t.label}
                </Button>
              ))}
            </SimpleGrid>
            {templateId !== 'blank' && (
              <Text size="xs" c="dimmed">{ONTOLOGY_TEMPLATES[templateId].description}</Text>
            )}
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Глоссарий" description="Термины">
          <Stack gap="md" mt="md">
            <Text size="sm" c="dimmed">По одному термину на строку или «термин;определение».</Text>
            <Textarea
              minRows={6}
              placeholder={'GPON;пассивная оптическая сеть\nOLT;линейный терминал'}
              value={glossaryLines}
              onChange={(e) => setGlossaryLines(e.currentTarget.value)}
            />
            <Button
              variant={importTools ? 'filled' : 'light'}
              size="xs"
              w="fit-content"
              onClick={() => setImportTools((v) => !v)}
            >
              {importTools ? '✓' : ''} Добавить термины из активных tools ({tools.filter((t) => t.is_active !== false).length})
            </Button>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Примеры" description="Запросы">
          <Stack gap="sm" mt="md">
            <Text size="sm" c="dimmed">Три типовых запроса — модель лучше понимает, какой tool вызывать.</Text>
            {examples.map((ex, i) => (
              <Group key={i} align="flex-end" wrap="nowrap">
                <TextInput
                  style={{ flex: 1 }}
                  label={`Пример ${i + 1}`}
                  placeholder="Запрос пользователя"
                  value={ex.query}
                  onChange={(e) => {
                    const next = [...examples];
                    next[i] = { ...next[i], query: e.currentTarget.value };
                    setExamples(next);
                  }}
                />
                <Select
                  w={200}
                  label="Tool"
                  placeholder="Необязательно"
                  data={toolOptions}
                  searchable
                  clearable
                  value={ex.expected_tool || null}
                  onChange={(v) => {
                    const next = [...examples];
                    next[i] = { ...next[i], expected_tool: v || '' };
                    setExamples(next);
                  }}
                />
              </Group>
            ))}
          </Stack>
        </Stepper.Step>

        <Stepper.Completed>
          <Text mt="md">Готово — нажмите «Создать», чтобы добавить секции в онтологию.</Text>
        </Stepper.Completed>
      </Stepper>

      <Group justify="space-between">
        <Button variant="default" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>Назад</Button>
        {step < 3 ? (
          <Button onClick={() => setStep((s) => s + 1)}>Далее</Button>
        ) : (
          <Button onClick={finish}>Создать</Button>
        )}
      </Group>
    </Modal>
  );
}
