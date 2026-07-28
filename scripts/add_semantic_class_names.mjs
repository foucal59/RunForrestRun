#!/usr/bin/env node
import fs from 'fs'
import path from 'path'
import parser from '@babel/parser'
import traverseModule from '@babel/traverse'
import generateModule from '@babel/generator'

const traverse = traverseModule.default
const generate = generateModule.default

const SRC_ROOT = path.resolve(process.cwd(), 'src')
const FILE_RE = /\.(jsx|js)$/

function toSnake(input = '') {
  return input
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^a-zA-Z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase()
}

function normalizeText(input = '') {
  return input
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, ' ')
    .trim()
}

function slugWords(input = '', maxWords = 3) {
  const words = normalizeText(input).toLowerCase().split(/\s+/).filter(Boolean)
  return words.slice(0, maxWords).join('_')
}

function getFiles(dir) {
  const results = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const filePath = path.join(dir, entry.name)
    if (entry.isDirectory()) results.push(...getFiles(filePath))
    else if (FILE_RE.test(entry.name)) results.push(filePath)
  }
  return results.sort()
}

function getAttr(node, name) {
  return node.attributes.find(attr => attr?.type === 'JSXAttribute' && attr.name?.name === name) || null
}

function getLiteralAttrValue(attr) {
  if (!attr?.value) return null
  if (attr.value.type === 'StringLiteral') return attr.value.value
  if (attr.value.type === 'JSXExpressionContainer') {
    const expr = attr.value.expression
    if (expr.type === 'TemplateLiteral') {
      return expr.quasis.map(q => q.value.cooked || '').join('_')
    }
  }
  return null
}

function extractText(node) {
  if (!node) return ''
  if (node.type === 'JSXText') return node.value || ''
  if (node.type === 'StringLiteral') return node.value || ''
  if (node.type === 'JSXExpressionContainer') {
    const expr = node.expression
    if (expr?.type === 'StringLiteral') return expr.value || ''
    if (expr?.type === 'TemplateLiteral') return expr.quasis.map(q => q.value.cooked || '').join(' ')
    return ''
  }
  if (node.type === 'JSXElement' || node.type === 'JSXFragment') {
    return (node.children || []).map(extractText).join(' ')
  }
  return ''
}

function expressionHint(expr) {
  if (!expr) return ''
  if (expr.type === 'Identifier') return toSnake(expr.name)
  if (expr.type === 'StringLiteral') return slugWords(expr.value, 3)
  if (expr.type === 'TemplateLiteral') {
    const parts = []
    for (const quasi of expr.quasis || []) {
      const slug = slugWords(quasi.value.cooked || '', 3)
      if (slug) parts.push(slug)
    }
    for (const item of expr.expressions || []) {
      const hint = expressionHint(item)
      if (hint) parts.push(hint)
    }
    return parts.filter(Boolean).slice(0, 3).join('_')
  }
  if (expr.type === 'MemberExpression' || expr.type === 'OptionalMemberExpression') {
    const objectHint = expressionHint(expr.object)
    const propertyHint = expr.property.type === 'Identifier'
      ? toSnake(expr.property.name)
      : expressionHint(expr.property)
    if (propertyHint === 'length' && objectHint) return `${objectHint}_count`
    return propertyHint || objectHint
  }
  if (expr.type === 'CallExpression' || expr.type === 'OptionalCallExpression') {
    const argHints = (expr.arguments || [])
      .map(arg => expressionHint(arg))
      .filter(Boolean)
    if (argHints.length) return argHints[0]
    return expressionHint(expr.callee)
  }
  if (expr.type === 'ConditionalExpression') {
    return expressionHint(expr.consequent) || expressionHint(expr.alternate) || expressionHint(expr.test)
  }
  if (expr.type === 'LogicalExpression' || expr.type === 'BinaryExpression') {
    return expressionHint(expr.left) || expressionHint(expr.right)
  }
  if (expr.type === 'UnaryExpression' || expr.type === 'AwaitExpression') {
    return expressionHint(expr.argument)
  }
  if (expr.type === 'ArrayExpression') {
    return (expr.elements || []).map(item => expressionHint(item)).filter(Boolean)[0] || ''
  }
  if (expr.type === 'ObjectExpression') {
    return (expr.properties || [])
      .map(prop => prop.type === 'ObjectProperty' ? expressionHint(prop.value) || expressionHint(prop.key) : '')
      .filter(Boolean)[0] || ''
  }
  return ''
}

function directContentSlug(node) {
  if (!node?.children?.length) return ''
  const hints = []
  for (const child of node.children) {
    if (child.type === 'JSXText') {
      const slug = slugWords(child.value, 4)
      if (slug) hints.push(slug)
      continue
    }
    if (child.type === 'JSXExpressionContainer') {
      const hint = expressionHint(child.expression)
      if (hint) hints.push(hint)
      continue
    }
    if (child.type === 'StringLiteral') {
      const slug = slugWords(child.value, 4)
      if (slug) hints.push(slug)
    }
  }
  return hints.filter(Boolean).slice(0, 3).join('_')
}

function semanticHintFromAttr(node) {
  const candidates = ['data-name', 'name', 'title', 'label', 'aria-label', 'id']
  for (const key of candidates) {
    const value = getLiteralAttrValue(getAttr(node, key))
    const slug = slugWords(value || '', 5)
    if (slug) return slug
  }
  return ''
}

function inferRole({ tagName, classText, textSlug, idSlug, typeSlug }) {
  const classes = classText.split(/\s+/).filter(Boolean)
  const has = (token) => classes.includes(token)
  const hasText = (fragment) => classText.includes(fragment)

  if (tagName === 'img') return hasText('rounded-full') ? 'avatar_image' : `${textSlug || 'image'}_image`
  if (tagName === 'hr') return 'divider'
  if (tagName === 'main') return 'main'
  if (tagName === 'nav') return 'nav'
  if (tagName === 'header') return 'header'
  if (tagName === 'footer') return 'footer'
  if (tagName === 'input') return `${idSlug || typeSlug || 'field'}_input`
  if (tagName === 'textarea') return `${idSlug || typeSlug || 'field'}_input`
  if (tagName === 'select') return `${idSlug || typeSlug || 'field'}_input`
  if (tagName === 'button') return `${textSlug || 'action'}_button`
  if (tagName === 'a' || tagName === 'link') return `${textSlug || 'action'}_link`
  if (/^h[1-6]$/.test(tagName)) return `${textSlug || 'section'}_title`
  if (tagName === 'table') return 'table'
  if (tagName === 'thead') return 'table_head'
  if (tagName === 'tbody') return 'table_body'
  if (tagName === 'tr') return hasText('cursor-pointer') ? 'table_row' : 'table_header_row'
  if (tagName === 'th') return `${textSlug || 'header'}_cell`
  if (tagName === 'td') return `${textSlug || 'value'}_cell`
  if (tagName === 'svg') return `${textSlug || 'icon'}_icon`
  if (tagName === 'p') {
    if (hasText('text-txt-muted')) return textSlug ? `${textSlug}_description` : 'description'
    return textSlug ? `${textSlug}_text` : 'text'
  }
  if (tagName === 'span') {
    if (hasText('font-mono')) return textSlug ? `${textSlug}_value` : 'value'
    if (hasText('text-txt-muted')) return textSlug ? `${textSlug}_meta` : 'meta'
    if (hasText('font-medium') || hasText('font-semibold')) return textSlug ? `${textSlug}_label` : 'label'
    return textSlug ? `${textSlug}_text` : 'text'
  }
  if (hasText('shadow-lg') && hasText('bg-white') && hasText('border')) return 'tooltip'
  if (hasText('overflow-x-auto') || hasText('overflow-y-auto') || hasText('overflow-auto')) return 'scroller'
  if (has('grid')) return 'grid'
  if (has('card')) return 'card'
  if (/\bspace-y-/.test(classText)) return 'list'
  if (has('animate-pulse')) return 'skeleton'
  if (has('items-center') && has('justify-between')) return 'header'
  if (has('min-w-0') && has('flex-1')) return 'details'
  if (has('text-right') && has('flex-shrink-0')) return 'metrics'
  if (hasText('font-mono')) return textSlug ? `${textSlug}_value` : 'value'
  if (hasText('uppercase')) return textSlug ? `${textSlug}_label` : 'label'
  if (tagName === 'div') {
    if (hasText('text-txt-muted')) return textSlug ? `${textSlug}_meta` : 'meta'
    if (hasText('font-medium') || hasText('font-semibold')) return textSlug ? `${textSlug}_label` : 'label'
    if (textSlug && /(title|details|voir|date|distance|temps|allure|fitness|fatigue|forme|vo2max|statut|pace|efficacite|bpm|km|legend|guide|name|zone|desc|rate)/.test(textSlug)) {
      return `${textSlug}_${/(date|pace|bpm|km|fitness|fatigue|forme|efficacite|vo2max|rate)/.test(textSlug) ? 'value' : 'text'}`
    }
    return textSlug ? `${textSlug}_section` : 'section'
  }
  return tagName || 'block'
}

function uniqName(baseName, counterMap) {
  const key = toSnake(baseName)
  const count = (counterMap.get(key) || 0) + 1
  counterMap.set(key, count)
  return count === 1 ? key : `${key}_${count}`
}

function appendSemanticClass(attr, semanticName) {
  if (!attr?.value) return null
  if (attr.value.type === 'StringLiteral') {
    const tokens = attr.value.value.split(/\s+/).filter(Boolean)
    if (!tokens.includes(semanticName)) tokens.push(semanticName)
    attr.value.value = tokens.join(' ')
    return attr
  }
  if (attr.value.type === 'JSXExpressionContainer' && attr.value.expression.type === 'TemplateLiteral') {
    const expr = attr.value.expression
    const staticText = expr.quasis.map(q => q.value.cooked || '').join(' ')
    if (!staticText.split(/\s+/).includes(semanticName)) {
      const last = expr.quasis[expr.quasis.length - 1]
      const needsSpace = !/\s$/.test(last.value.raw)
      last.value.raw += `${needsSpace ? ' ' : ''}${semanticName}`
      last.value.cooked += `${needsSpace ? ' ' : ''}${semanticName}`
    }
    return attr
  }
  return null
}

function createDataNameAttr(semanticName) {
  return ` data-name="${semanticName}"`
}

function fileBaseName(filePath) {
  const rel = path.relative(SRC_ROOT, filePath)
  const dir = path.dirname(rel)
  const base = path.basename(rel, path.extname(rel))
  if (base.toLowerCase() === 'index') return toSnake(dir)
  return toSnake(base)
}

for (const filePath of getFiles(SRC_ROOT)) {
  const source = fs.readFileSync(filePath, 'utf8')
  const ast = parser.parse(source, { sourceType: 'module', plugins: ['jsx'] })
  const replacements = []
  const elementNames = new WeakMap()
  const counterMap = new Map()
  const baseName = fileBaseName(filePath)

  function nearestParentName(pathRef) {
    let current = pathRef.parentPath
    while (current) {
      if (current.isJSXElement()) {
        const opening = current.node.openingElement
        const known = elementNames.get(opening)
        if (known) return known
        const existing = getLiteralAttrValue(getAttr(opening, 'data-name'))
        if (existing) return toSnake(existing.replace(/_+$/g, ''))
        const hinted = semanticHintFromAttr(opening)
        if (hinted) return hinted
      }
      current = current.parentPath
    }
    return baseName
  }

  traverse(ast, {
    JSXOpeningElement(pathRef) {
      const node = pathRef.node
      const classAttr = getAttr(node, 'className')
      if (!classAttr?.value) return

      const tagName = node.name.type === 'JSXIdentifier'
        ? toSnake(node.name.name)
        : 'component'

      const dataAttr = getAttr(node, 'data-name')
      const existingDataName = getLiteralAttrValue(dataAttr)
      const textSlug = directContentSlug(pathRef.parent) || slugWords(extractText(pathRef.parent), 3)
      const idSlug = slugWords(getLiteralAttrValue(getAttr(node, 'id')) || getLiteralAttrValue(getAttr(node, 'name')) || '', 3)
      const typeSlug = slugWords(getLiteralAttrValue(getAttr(node, 'type')) || '', 2)

      let classText = ''
      if (classAttr.value.type === 'StringLiteral') classText = classAttr.value.value
      else if (classAttr.value.type === 'JSXExpressionContainer' && classAttr.value.expression.type === 'TemplateLiteral') {
        classText = classAttr.value.expression.quasis.map(q => q.value.cooked || '').join(' ')
      } else {
        if (existingDataName) elementNames.set(node, toSnake(existingDataName.replace(/_+$/g, '')))
        return
      }

      let semanticName = existingDataName ? toSnake(existingDataName.replace(/_+$/g, '')) : null
      if (!semanticName) {
        const parentName = nearestParentName(pathRef)
        const role = inferRole({ tagName, classText, textSlug, idSlug, typeSlug })
        semanticName = uniqName(`${parentName}_${role}`, counterMap)
      }
      elementNames.set(node, semanticName)

      const newAttr = appendSemanticClass(classAttr, semanticName)
      if (newAttr) {
        const code = `className=${generate(newAttr.value, { jsescOption: { minimal: true } }).code}`
        replacements.push({ start: classAttr.start, end: classAttr.end, text: code })
      }

      if (!dataAttr) {
        replacements.push({ start: classAttr.end, end: classAttr.end, text: createDataNameAttr(semanticName) })
      }
    }
  })

  if (!replacements.length) continue
  replacements.sort((a, b) => b.start - a.start)
  let output = source
  for (const op of replacements) {
    output = output.slice(0, op.start) + op.text + output.slice(op.end)
  }
  fs.writeFileSync(filePath, output)
}
