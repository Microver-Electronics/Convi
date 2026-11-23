{ ==========================================================================
  Convi schematic builder: SchLib + SchDoc commands (see convi/convert/sch_convert.py)
  Coordinates arrive as milli-mils (VD) and are converted with MilsToCoord.
  ========================================================================== }

var
  SchDoc      : Variant;    { ISch_Document (SchDoc or SchLib) }
  SDoc        : Variant;    { IServerDocument }
  CurSComp    : Variant;
  InSComp     : Boolean;
  CurSheetSym : Variant;
  CurHConn    : Variant;    { harness connector being filled (HCONN ... HCONNEND) }
  HConnOk     : Boolean;
  HConnType   : String;
  HConnTX     : Double;
  HConnTY     : Double;
  FontCache   : TStringList;
  IsLib       : Boolean;
  AccessErr   : Boolean;    { UpdatePrimitivesAccessibility failed once (reported once) }

procedure FinishSpecific;
begin
end;

procedure InitSpecific;
begin
  FontCache := TStringList.Create;
  InSComp := False;
  AccessErr := False;
end;

{ Mark the symbol's graphics, texts and pins as part of the symbol (ISNOTACCESIBLE=T in the file,
  as Altium writes for every placed or library symbol). Without it a click picks a single line or
  arc and drags it out of the part. Altium's own KiCad importer makes the same call.
  Probe: devtools/probes/p67_access.pas. }
procedure LockPrimitives(C: Variant);
begin
  try
    C.UpdatePrimitivesAccessibility;
  except
    if not AccessErr then Err('symbol primitives stay selectable one by one: ' + ExceptionMessage);
    AccessErr := True;
  end;
end;

function MX(K: String): Integer;
begin
  Result := MilsToCoord(VD(K, 0));
end;

function PtXY(X, Y: Double): TLocation;
begin
  Result := Point(MilsToCoord(X), MilsToCoord(Y));
end;

function FontOf: Integer;
var Key, Nm: String; i, Sz: Integer;
begin
  Nm := V('font');
  if Nm = '' then Nm := 'Arial';
  Sz := VI('size', 8);
  Key := Nm + '|' + IntToStr(Sz) + '|' + F.Values['bold'] + '|' + F.Values['italic'];
  i := FontCache.IndexOfName(Key);
  if i >= 0 then
  begin
    Result := StrToInt(FontCache.ValueFromIndex[i]);
    Exit;
  end;
  Result := SchServer.FontManager.GetFontID(Sz, 0, False, VB('italic'), VB('bold'), False, Nm);
  FontCache.Add(Key + '=' + IntToStr(Result));
end;

procedure AddSch(Obj: Variant);
begin
  if InSComp then
    CurSComp.AddSchObject(Obj)
  else
    SchDoc.RegisterSchObjectInContainer(Obj);
end;

procedure SetOwner(Obj: Variant);
begin
  if InSComp then
  begin
    Obj.OwnerPartId := VI('part', 0);
    Obj.OwnerPartDisplayMode := VI('mode', 0);
  end;
end;

{ locale independent float parse for 'nnn.nnnn' }
function StrToFloatDot(S: String): Double;
var k: Integer; IntPart, FracPart: String; Neg: Boolean; R, Den: Double; i: Integer;
begin
  S := Trim(S);
  Neg := (Length(S) > 0) and (S[1] = '-');
  if Neg then S := Copy(S, 2, 100);
  k := Pos('.', S);
  if k = 0 then
    R := StrToInt(S)
  else
  begin
    IntPart := Copy(S, 1, k - 1);
    FracPart := Copy(S, k + 1, 100);
    if IntPart = '' then IntPart := '0';
    R := StrToInt(IntPart);
    if FracPart <> '' then
    begin
      Den := 1;
      for i := 1 to Length(FracPart) do Den := Den * 10;
      R := R + StrToInt(FracPart) / Den;
    end;
  end;
  if Neg then R := -R;
  Result := R;
end;

{ parse "x,y;x,y;..." (milli-mil floats as text with '.') }
procedure AddVertices(Obj: Variant; S: String);
var L: TStringList; i, k: Integer; X, Y: Double; P: String;
begin
  L := TStringList.Create;
  L.Delimiter := ';'; L.StrictDelimiter := True;
  L.DelimitedText := S;
  for i := 0 to L.Count - 1 do
  begin
    P := L[i];
    k := Pos(',', P);
    X := StrToFloatDot(Copy(P, 1, k - 1));
    Y := StrToFloatDot(Copy(P, k + 1, 50));
    Obj.InsertVertex := i + 1;
    Obj.SetState_Vertex(i + 1, PtXY(X, Y));
  end;
  L.Free;
end;

function LineW: Integer;
begin
  case VI('lw', 1) of
    0: Result := eZeroSize;
    1: Result := eSmall;
    2: Result := eMedium;
  else
    Result := eLarge;
  end;
end;

{ ------------------------------------------------------------------ documents }
procedure CloseIfOpenS(Path: String);
var D: Variant;
begin
  try
    D := Client.GetDocumentByPath(Path);
    if D <> Nil then
    begin
      D.SetModified(False);
      Client.CloseDocument(D);
    end;
  except
  end;
end;

procedure DoSchDoc;
begin
  IsLib := False;
  CloseIfOpenS(V('path'));
  SDoc := CreateNewDocumentFromDocumentKind('SCH');
  SchDoc := SchServer.GetCurrentSchDocument;
  SDoc.SetFileName(V('path'));
  try
    SchDoc.UseCustomSheet := True;
    SchDoc.CustomX := MX('w');
    SchDoc.CustomY := MX('h');
    SchDoc.ReferenceZonesOn := True;
  except
    Err('sheet size: ' + ExceptionMessage);
  end;
  Log('new SchDoc ' + V('path'));
end;

procedure DoSaveSch;
begin
  try
    SchDoc.GraphicallyInvalidate;
  except
  end;
  SDoc.DoFileSave('');
  Log('saved ' + SDoc.FileName);
  if VB('close') then Client.CloseDocument(SDoc);
end;

procedure DoSchLib;
begin
  IsLib := True;
  CloseIfOpenS(V('path'));
  if FileExists(V('path')) then DeleteFile(V('path'));
  SDoc := Client.OpenNewDocument('SCHLIB', V('path'), '', False);
  Client.ShowDocument(SDoc);
  SchDoc := SchServer.GetCurrentSchDocument;
  Log('new SchLib ' + V('path'));
end;

procedure DoSaveSchLib;
var It, C, First: Variant; Del: TInterfaceList; i: Integer;
begin
  { remove the default empty component created with a new library }
  Del := TInterfaceList.Create;
  It := SchDoc.SchLibIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eSchComponent));
  C := It.FirstSchObject;
  while C <> Nil do
  begin
    if (C.LibReference = 'Component_1') or (C.LibReference = '') then Del.Add(C);
    C := It.NextSchObject;
  end;
  SchDoc.SchIterator_Destroy(It);
  for i := 0 to Del.Count - 1 do SchDoc.RemoveSchComponent(Del.Items[i]);
  SDoc.DoFileSave('SchLib');
  Log('saved library ' + SDoc.FileName);
  if VB('close') then Client.CloseDocument(SDoc);
end;

{ Sheet template: copy the template's sheet settings and graphics into the current sheet.
  Altium's own template commands open a modal 'Update Template' dialog, which would stop the
  build, so the template is applied here object by object.  'src' is a copy of the .SchDot
  saved as .SchDoc (opened read-only and closed again); 'path' is the .SchDot inside the
  output project, recorded as the sheet's template. }
procedure DoTemplate;
var TDoc, TSch, It, O, C, Tpl: Variant; Src, TPath: String; Added, Failed: Integer;
begin
  Src := V('src');
  TPath := V('path');
  TSch := Nil;
  try
    TDoc := Client.OpenDocument('SCH', Src);
    if TDoc <> Nil then Client.ShowDocument(TDoc);
    TSch := SchServer.GetSchDocumentByPath(Src);
  except
    Err('template: cannot open ' + Src + ': ' + ExceptionMessage);
  end;
  if TSch = Nil then
  begin
    Err('template: ' + Src + ' did not open as a schematic; the sheet keeps the KiCad paper size');
    Client.ShowDocument(SDoc);
    Exit;
  end;
  { sheet size, orientation and border from the template; its graphics replace the title block }
  try SchDoc.UseCustomSheet := TSch.UseCustomSheet; except Err('template: UseCustomSheet'); end;
  try SchDoc.SheetStyle := TSch.SheetStyle; except Err('template: SheetStyle'); end;
  try
    SchDoc.CustomX := TSch.CustomX;
    SchDoc.CustomY := TSch.CustomY;
  except
  end;
  try SchDoc.WorkspaceOrientation := TSch.WorkspaceOrientation; except end;
  try SchDoc.BorderOn := TSch.BorderOn; except end;
  try SchDoc.ReferenceZonesOn := TSch.ReferenceZonesOn; except end;
  try SchDoc.TitleBlockOn := False; except Err('template: TitleBlockOn'); end;
  try SchDoc.ShowTemplateGraphics := True; except end;
  { the template container makes Altium treat the graphics as the sheet's template }
  Tpl := Nil;
  try
    Tpl := SchServer.SchObjectFactory(eTemplate, eCreate_Default);
    Tpl.FileName := TPath;
    SchDoc.AddSchObject(Tpl);
  except
    Tpl := Nil;
    Err('template: no template object (' + ExceptionMessage + '); graphics copied as plain objects');
  end;
  try SchDoc.TemplateFileName := TPath; except end;
  Added := 0;
  Failed := 0;
  It := TSch.SchIterator_Create;
  It.SetState_IterationDepth(eIterateFirstLevel);
  O := It.FirstSchObject;
  while O <> Nil do
  begin
    { the template's own parameters are not copied: the sheet keeps the KiCad title block values }
    if O.ObjectId <> eParameter then
    begin
      try
        C := O.Replicate;
        if Tpl <> Nil then Tpl.AddSchObject(C)
        else SchDoc.RegisterSchObjectInContainer(C);
        Added := Added + 1;
      except
        Failed := Failed + 1;
      end;
    end;
    O := It.NextSchObject;
  end;
  TSch.SchIterator_Destroy(It);
  try
    TDoc.SetModified(False);
    Client.CloseDocument(TDoc);
  except
  end;
  Client.ShowDocument(SDoc);
  if Failed > 0 then Err('template: ' + IntToStr(Failed) + ' template object(s) could not be copied');
  Log('template ' + TPath + ': ' + IntToStr(Added) + ' object(s)');
  CountStat('template_object');
end;

{ Move the whole drawing (everything but the template and the sheet parameters) by dx, dy so
  it sits inside the template's border and clear of its title block (see sch_convert). }
procedure DoShift;
var It, O: Variant; L: TInterfaceList; i, Moved, Failed: Integer; DX, DY: Integer; IsTpl: Boolean;
begin
  DX := MX('dx');
  DY := MX('dy');
  L := TInterfaceList.Create;
  It := SchDoc.SchIterator_Create;
  It.SetState_IterationDepth(eIterateFirstLevel);
  O := It.FirstSchObject;
  while O <> Nil do
  begin
    IsTpl := False;
    try
      IsTpl := (O.ObjectId = eTemplate) or (O.ObjectId = eParameter);
    except
    end;
    if not IsTpl then L.Add(O);
    O := It.NextSchObject;
  end;
  SchDoc.SchIterator_Destroy(It);
  Moved := 0;
  Failed := 0;
  for i := 0 to L.Count - 1 do
  begin
    try
      L.Items[i].MoveByXY(DX, DY);
      Moved := Moved + 1;
    except
      Failed := Failed + 1;
    end;
  end;
  if Failed > 0 then Err('shift: ' + IntToStr(Failed) + ' object(s) could not be moved');
  Log('shift ' + IntToStr(VI('dy', 0) div 1000) + ' mil: ' + IntToStr(Moved) + ' object(s)');
end;

{ sheet parameter: update the value when the sheet already has one of that name }
procedure DoDocParam;
var It, P: Variant; Found: Boolean;
begin
  Found := False;
  It := SchDoc.SchIterator_Create;
  It.SetState_IterationDepth(eIterateFirstLevel);
  It.AddFilter_ObjectSet(MkSet(eParameter));
  P := It.FirstSchObject;
  while (P <> Nil) and (not Found) do
  begin
    if UpperCase(P.Name) = UpperCase(V('name')) then
    begin
      P.Text := V('value');
      Found := True;
    end;
    P := It.NextSchObject;
  end;
  SchDoc.SchIterator_Destroy(It);
  if Found then Exit;
  P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
  P.Name := V('name');
  P.Text := V('value');
  SchDoc.AddSchObject(P);
end;

{ ------------------------------------------------------------------ library components }
procedure DoLComp;
begin
  CurSComp := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
  CurSComp.LibReference := V('name');
  CurSComp.ComponentDescription := V('desc');
  CurSComp.Designator.Text := V('des');
  CurSComp.Comment.Text := V('comment');
  { PartCount = real unit count. Altium stores it in the file as units + 1 itself
    (native single-part symbol: PARTCOUNT=2). Writing units + 1 here gave every
    symbol a phantom extra part: ERC 'unused sub-part' and designators like U501A. }
  CurSComp.PartCount := VI('parts', 1);
  CurSComp.CurrentPartID := 1;
  CurSComp.DisplayMode := 0;
  try
    CurSComp.DisplayModeCount := VI('modes', 1);
  except
  end;
  InSComp := True;
end;

procedure DoLCompEnd;
begin
  InSComp := False;
  LockPrimitives(CurSComp);
  SchDoc.AddSchComponent(CurSComp);
  CountStat('symbol');
end;

procedure DoLParam;
var P: Variant;
begin
  P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
  P.Name := V('name');
  P.Text := V('value');
  P.IsHidden := VB('hidden');
  CurSComp.AddSchObject(P);
end;

{ ------------------------------------------------------------------ placed components }
function CompKindOf(S: String): Integer;
begin
  if S = 'graphical' then Result := eComponentKind_Graphical
  else if S = 'standard_nobom' then Result := eComponentKind_Standard_NoBOM
  else if S = 'mechanical' then Result := eComponentKind_Mechanical
  else Result := eComponentKind_Standard;
end;

procedure DoCBegin;
begin
  CurSComp := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
  CurSComp.LibReference := V('libref');
  CurSComp.SourceLibraryName := V('lib');
  try
    CurSComp.DesignItemId := V('libref');
    CurSComp.SymbolReference := V('libref');
  except
  end;
  CurSComp.PartCount := VI('parts', 1);   { real unit count, see DoLComp }
  CurSComp.CurrentPartID := VI('part', 1);
  try
    CurSComp.DisplayModeCount := VI('modes', 1);
  except
  end;
  CurSComp.DisplayMode := VI('mode', 0);
  CurSComp.Location := PtXY(VD('x', 0), VD('y', 0));
  CurSComp.ComponentDescription := V('desc');
  try
    CurSComp.ComponentKind := CompKindOf(V('kind'));
  except
  end;
  SchDoc.RegisterSchObjectInContainer(CurSComp);
  CurSComp.UniqueId := V('uid');
  CurSComp.Designator.Text := V('des');
  InSComp := True;
end;

procedure SetComplexText(T: Variant);
begin
  T.Text := V('text');
  try
    T.Autoposition := False;
  except
  end;
  T.Location := PtXY(VD('x', 0), VD('y', 0));
  if VI('orient', 0) = 1 then T.Orientation := eRotate90 else T.Orientation := eRotate0;
  T.Justification := VI('just', 0);
  T.FontId := FontOf;
  T.IsHidden := VB('hidden');
end;

procedure DoCDes;
begin
  SetComplexText(CurSComp.Designator);
end;

procedure DoCComment;
begin
  SetComplexText(CurSComp.Comment);
end;

procedure DoCParam;
var P: Variant;
begin
  P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
  P.Name := V('name');
  P.Text := V('value');
  CurSComp.AddSchObject(P);
  SetComplexText(P);
  P.Text := V('value');
  try
    P.ShowName := VB('shownm');
  except
  end;
end;

procedure DoCImpl;
var Impl: Variant;
begin
  Impl := CurSComp.AddSchImplementation;
  Impl.ModelName := V('model');
  Impl.ModelType := 'PCBLIB';
  if Has('current') then Impl.IsCurrent := VB('current') else Impl.IsCurrent := True;
  try
    Impl.AddDataFileLink(V('model'), V('lib'), 'PCBLib');
  except
    Err('footprint link: ' + ExceptionMessage);
  end;
end;

procedure DoCEnd;
begin
  InSComp := False;
  CurSComp.SetState_OrientationWithoutRotating(VI('orient', 0));
  CurSComp.IsMirrored := VB('mirror');
  LockPrimitives(CurSComp);
  CurSComp.GraphicallyInvalidate;
  CountStat('component');
end;

{ ------------------------------------------------------------------ primitives }
procedure DoSPin;
var P: Variant;
begin
  P := SchServer.SchObjectFactory(ePin, eCreate_Default);
  P.Location := PtXY(VD('x', 0), VD('y', 0));
  P.Orientation := VI('orient', 0);
  P.PinLength := MX('len');
  P.Designator := V('des');
  P.Name := V('name');
  P.Electrical := VI('etype', 4);
  P.ShowName := VB('showname');
  P.ShowDesignator := VB('showdes');
  P.IsHidden := VB('hidden');
  if V('hiddennet') <> '' then P.HiddenNetName := V('hiddennet');
  try
    P.Name_FontMode := ePinItemMode_Custom;
    P.Name_CustomFontID := SchServer.FontManager.GetFontID(VI('nsize', 7), 0, False, False, False, False, 'Arial');
    P.Designator_FontMode := ePinItemMode_Custom;
    P.Designator_CustomFontID := SchServer.FontManager.GetFontID(VI('dsize', 7), 0, False, False, False, False, 'Arial');
    P.Name_PositionMode := ePinItemMode_Custom;
    P.Name_CustomPosition_Margin := MX('margin');
  except
  end;
  if VI('inner', 0) <> 0 then P.Symbol_InnerEdge := VI('inner', 0);
  if VI('outeredge', 0) <> 0 then P.Symbol_OuterEdge := VI('outeredge', 0);
  if VI('outer', 0) <> 0 then P.Symbol_Outer := VI('outer', 0);
  SetOwner(P);
  AddSch(P);
  CountStat('pin');
end;

procedure SetGraphic(G: Variant);
begin
  try
    G.Color := VI('color', 128);
    G.LineWidth := LineW;
  except
  end;
  SetOwner(G);
end;

procedure DoSRect;
var R: Variant;
begin
  R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
  R.Location := PtXY(VD('x1', 0), VD('y1', 0));
  R.Corner := PtXY(VD('x2', 0), VD('y2', 0));
  SetGraphic(R);
  R.IsSolid := VB('solid');
  if VB('solid') then R.AreaColor := VI('area', 0);
  try
    if VI('style', 0) > 0 then R.LineStyle := VI('style', 0);
  except
  end;
  AddSch(R);
end;

procedure DoSPolyline;
var P: Variant;
begin
  P := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
  AddVertices(P, V('pts'));
  SetGraphic(P);
  try
    if VI('style', 0) > 0 then P.LineStyle := VI('style', 0);
  except
  end;
  AddSch(P);
end;

procedure DoSPolygon;
var P: Variant;
begin
  P := SchServer.SchObjectFactory(ePolygon, eCreate_Default);
  AddVertices(P, V('pts'));
  SetGraphic(P);
  P.IsSolid := VB('solid');
  P.AreaColor := VI('area', 0);
  AddSch(P);
end;

procedure DoSCircle;
var E: Variant;
begin
  E := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
  E.Location := PtXY(VD('x', 0), VD('y', 0));
  E.Radius := MX('r');
  E.SecondaryRadius := MX('r');
  SetGraphic(E);
  E.IsSolid := VB('solid');
  if VB('solid') then E.AreaColor := VI('area', 0) else E.Transparent := True;
  AddSch(E);
end;

procedure DoSArc;
var A: Variant;
begin
  A := SchServer.SchObjectFactory(eArc, eCreate_Default);
  A.Location := PtXY(VD('x', 0), VD('y', 0));
  A.Radius := MX('r');
  A.StartAngle := VD('a1', 0);
  A.EndAngle := VD('a2', 0);
  SetGraphic(A);
  AddSch(A);
end;

procedure DoSBezier;
var B: Variant;
begin
  B := SchServer.SchObjectFactory(eBezier, eCreate_Default);
  AddVertices(B, V('pts'));
  SetGraphic(B);
  AddSch(B);
end;

procedure DoSLabel;
var T: Variant;
begin
  T := SchServer.SchObjectFactory(eLabel, eCreate_Default);
  T.Location := PtXY(VD('x', 0), VD('y', 0));
  T.Text := V('text');
  T.Orientation := VI('orient', 0);
  T.Justification := VI('just', 0);
  T.FontId := FontOf;
  try
    T.Color := VI('color', 128);
  except
  end;
  SetOwner(T);
  AddSch(T);
end;

procedure DoSTextFrame;
var T: Variant;
begin
  T := SchServer.SchObjectFactory(eTextFrame, eCreate_Default);
  T.Location := PtXY(VD('x1', 0), VD('y1', 0));
  T.Corner := PtXY(VD('x2', 0), VD('y2', 0));
  T.Text := V('text');
  T.ShowBorder := VB('border');
  T.WordWrap := True;
  { KiCad text has no background.  Altium's default solid white frame hid the wires,
    ports and parts underneath it (UART_RX port next to R520 on 05_usb_uart). }
  try
    T.IsSolid := False;
  except
    Err('text frame: IsSolid not settable');
  end;
  try
    T.ClipToRect := False;
  except
  end;
  T.FontId := FontOf;
  if VI('align', 0) <> 0 then
  begin
    { KiCad table cell justification: 1 centre, 2 right (Altium text frames default to left) }
    try
      if VI('align', 0) = 1 then T.Alignment := eHorizontalCentreAlign
      else T.Alignment := eRightAlign;
    except
      Err('text frame: Alignment not settable');
    end;
  end;
  SetOwner(T);
  AddSch(T);
end;

{ ------------------------------------------------------------------ connectivity }
procedure DoWire(IsBus: Boolean);
var W: Variant;
begin
  if IsBus then W := SchServer.SchObjectFactory(eBus, eCreate_Default)
  else W := SchServer.SchObjectFactory(eWire, eCreate_Default);
  AddVertices(W, V('pts'));
  // KiCad net class wire colour / width (Convi sends them when the class sets them)
  if VI('lw', 1) = 0 then W.LineWidth := eSmallest
  else if VI('lw', 1) = 2 then W.LineWidth := eMedium
  else if VI('lw', 1) = 3 then W.LineWidth := eLarge
  else W.LineWidth := eSmall;
  if VI('color', -1) >= 0 then W.Color := VI('color', 0);
  SchDoc.RegisterSchObjectInContainer(W);
  if IsBus then CountStat('bus') else CountStat('wire');
end;

procedure DoBusEntry;
var B: Variant;
begin
  B := SchServer.SchObjectFactory(eBusEntry, eCreate_Default);
  B.Location := PtXY(VD('x1', 0), VD('y1', 0));
  B.Corner := PtXY(VD('x2', 0), VD('y2', 0));
  SchDoc.RegisterSchObjectInContainer(B);
end;

procedure DoJunction;
var J: Variant;
begin
  J := SchServer.SchObjectFactory(eJunction, eCreate_Default);
  J.Location := PtXY(VD('x', 0), VD('y', 0));
  J.Locked := True;
  SchDoc.RegisterSchObjectInContainer(J);
end;

procedure DoNoErc;
var N: Variant;
begin
  N := SchServer.SchObjectFactory(eNoERC, eCreate_Default);
  N.Location := PtXY(VD('x', 0), VD('y', 0));
  SchDoc.RegisterSchObjectInContainer(N);
end;

procedure DoNetLabel;
var N: Variant;
begin
  N := SchServer.SchObjectFactory(eNetlabel, eCreate_Default);
  N.Location := PtXY(VD('x', 0), VD('y', 0));
  N.Text := V('text');
  N.Orientation := VI('orient', 0);
  N.Justification := VI('just', 0);
  N.FontId := FontOf;
  SchDoc.RegisterSchObjectInContainer(N);
  CountStat('netlabel');
end;

procedure DoPower;
var P: Variant; St: String;
begin
  P := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
  P.Location := PtXY(VD('x', 0), VD('y', 0));
  P.Text := V('text');
  St := V('style');
  if St = 'gnd' then P.Style := ePowerGndPower
  else if St = 'signalgnd' then P.Style := ePowerGndSignal
  else if St = 'earth' then P.Style := ePowerGndEarth
  else if St = 'arrow' then P.Style := ePowerArrow
  else if St = 'circle' then P.Style := ePowerCircle
  else P.Style := ePowerBar;
  P.Orientation := VI('orient', 1);
  P.ShowNetName := VB('show');
  try
    P.FontId := FontOf;
  except
  end;
  SchDoc.RegisterSchObjectInContainer(P);
  CountStat('power');
end;

{ ------------------------------------------------------------------ signal harnesses
  KiCad group buses (Ethernet_P0 with bus alias 10GbE: unrelated nets bundled) are Altium signal harnesses: a harness
  connector whose entries take the member wires, signal harness lines, and ports / sheet entries of the
  harness type.  Property names follow the file records (HarnessConnectorSide, PrimaryConnectionPosition,
  DistanceFromTop, HarnessType); each is tried with its alternatives and a failure is logged. }
procedure DoSigHarness;
var H: Variant;
begin
  try
    H := SchServer.SchObjectFactory(eSignalHarness, eCreate_Default);
    AddVertices(H, V('pts'));
    SchDoc.RegisterSchObjectInContainer(H);
    CountStat('sigharness');
  except
    Err('signal harness: ' + ExceptionMessage);
  end;
end;

procedure DoHConn;
begin
  HConnOk := False;
  try
    CurHConn := SchServer.SchObjectFactory(eHarnessConnector, eCreate_Default);
    CurHConn.Location := PtXY(VD('x', 0), VD('y', 0));
    CurHConn.XSize := MX('w');
    CurHConn.YSize := MX('h');
  except
    Err('harness connector: ' + ExceptionMessage);
    Exit;
  end;
  { side: Altium's default (entries left, harness connection right); no script property sets it, Convi
    mirrors the side=right ones in the saved file (convi/altium/schdoc_patch.py) }
  try
    CurHConn.PrimaryConnectionPosition := MX('tip');
  except
    Err('harness connector: cannot set the harness connection position');
  end;
  SchDoc.RegisterSchObjectInContainer(CurHConn);
  HConnType := V('type');
  HConnTX := VD('tx', 0);
  HConnTY := VD('ty', 0);
  HConnOk := True;
  CountStat('harnessconnector');
end;

procedure DoHEntry;
var E: Variant;
begin
  if not HConnOk then Exit;
  try
    E := SchServer.SchObjectFactory(eHarnessEntry, eCreate_Default);
    E.Name := V('name');
    E.DistanceFromTop := MX('dist');
    try
      E.TextFontId := FontOf;
    except
    end;
    CurHConn.AddSchObject(E);
    CountStat('harnessentry');
  except
    Err('harness entry ' + V('name') + ': ' + ExceptionMessage);
  end;
end;

{ the harness type label: a child object of the connector (Altium creates it with the connector, text *,
  at 0,0).  Set after the entries (set before them it did not stick in AD24); its place under the box. }
procedure DoHConnEnd;
var T, It, O: Variant; Done: Boolean;
begin
  if not HConnOk then Exit;
  Done := False;
  try
    T := CurHConn.GetState_HarnessConnectorType;
    if HConnType <> '' then T.Text := HConnType;
    T.Location := PtXY(HConnTX, HConnTY);
    Done := True;
  except
  end;
  { the same object through the connector's own iterator as well: the GetState route raised nothing in
    AD24 yet the file kept '*' }
  try
    It := CurHConn.SchIterator_Create;
    It.AddFilter_ObjectSet(MkSet(eHarnessConnectorType));
    O := It.FirstSchObject;
    if O <> Nil then
    begin
      if HConnType <> '' then O.Text := HConnType;
      O.Location := PtXY(HConnTX, HConnTY);
      Done := True;
    end;
    CurHConn.SchIterator_Destroy(It);
  except
  end;
  if not Done then Err('harness connector: cannot set the harness type label');
  { the type label is hidden: stacked connectors are only 100 mil apart and the label below one box lands
    on the next; the type still shows on the ports, sheet entries and in the properties }
  Done := False;
  try
    CurHConn.HideHarnessConnectorType := True;
    Done := True;
  except
  end;
  if not Done then
  try
    T := CurHConn.GetState_HarnessConnectorType;
    T.IsHidden := True;
    Done := True;
  except
  end;
  if not Done then
  try
    T := CurHConn.GetState_HarnessConnectorType;
    T.FontId := FontOf;
  except
  end;
  try
    CurHConn.GraphicallyInvalidate;
  except
  end;
end;

procedure SetHarnessType(O: Variant);
begin
  if V('htype') = '' then Exit;
  try
    O.HarnessType := V('htype');
  except
    Err('harness type ' + V('htype') + ' not set on ' + V('text') + V('name'));
  end;
end;

procedure DoPort;
var P: Variant; St: String;
begin
  P := SchServer.SchObjectFactory(ePort, eCreate_Default);
  P.Location := PtXY(VD('x', 0), VD('y', 0));
  P.Name := V('text');
  P.IOType := VI('io', 0);
  St := V('style');
  if St = 'left' then P.Style := ePortLeft
  else if St = 'right' then P.Style := ePortRight
  else if St = 'top' then P.Style := ePortTop
  else if St = 'bottom' then P.Style := ePortBottom
  else P.Style := ePortNone;
  P.Width := MX('w');
  P.Height := MilsToCoord(100);
  P.Alignment := eHorizontalCentreAlign;
  try
    P.AutoSize := False;
    P.FontId := FontOf;
  except
  end;
  if Has('htype') then SetHarnessType(P);
  SchDoc.RegisterSchObjectInContainer(P);
  CountStat('port');
end;

procedure DoParamSet;
var PS, P: Variant;
begin
  PS := SchServer.SchObjectFactory(eParameterSet, eCreate_Default);
  PS.Location := PtXY(VD('x', 0), VD('y', 0));
  PS.Orientation := VI('orient', 0);
  P := SchServer.SchObjectFactory(eParameter, eCreate_Default);
  P.Name := V('name');
  P.Text := V('value');
  PS.AddSchObject(P);
  SchDoc.RegisterSchObjectInContainer(PS);
end;

procedure DoSheetSym;
var SS: Variant; X, Y: Integer;
begin
  SS := SchServer.SchObjectFactory(eSheetSymbol, eCreate_Default);
  X := MilsToCoord(VD('x', 0)); Y := MilsToCoord(VD('y', 0));
  SS.Location := Point(X, Y);
  SS.XSize := MX('w');
  SS.YSize := MX('h');
  try
    if VI('fill', -1) < 0 then SS.IsSolid := False
    else
    begin
      SS.IsSolid := True;
      SS.AreaColor := VI('fill', 0);
    end;
  except
  end;
  SchDoc.RegisterSchObjectInContainer(SS);
  SS.UniqueId := V('uid');
  SS.SheetName.Text := V('name');
  SS.SheetFileName.Text := V('file');
  try
    if V('nfont') <> '' then
      SS.SheetName.FontId := SchServer.FontManager.GetFontID(VI('nsize', 8), 0, False, VB('nitalic'),
        VB('nbold'), False, V('nfont'));
    if V('ffont') <> '' then
      SS.SheetFileName.FontId := SchServer.FontManager.GetFontID(VI('fsize', 8), 0, False, VB('fitalic'),
        VB('fbold'), False, V('ffont'));
  except
  end;
  try
    SS.SheetName.Autoposition := False;
    SS.SheetName.Location := Point(X, Y + MilsToCoord(20));
    SS.SheetFileName.Autoposition := False;
    SS.SheetFileName.Location := Point(X, Y - MX('h') - MilsToCoord(100));
  except
  end;
  CurSheetSym := SS;
  CountStat('sheetsymbol');
end;

procedure DoSheetEntry;
var E: Variant; S: String;
begin
  E := SchServer.SchObjectFactory(eSheetEntry, eCreate_Default);
  E.Name := V('name');
  S := V('side');
  if S = 'right' then E.Side := eRightSide
  else if S = 'top' then E.Side := eTopSide
  else if S = 'bottom' then E.Side := eBottomSide
  else E.Side := eLeftSide;
  E.DistanceFromTop := MX('dist');
  E.IOType := VI('io', 0);
  { entry label in the same font as every other converted text (Arial, KiCad size); Altium's default
    would be the document font, Times New Roman }
  try
    if Has('size') then E.TextFontId := FontOf;
  except
  end;
  if Has('htype') then SetHarnessType(E);
  CurSheetSym.AddSchObject(E);
end;

procedure DoSheetSymEnd;
begin
  try
    CurSheetSym.GraphicallyInvalidate;
  except
  end;
end;

procedure DoImage;
var I: Variant;
begin
  I := SchServer.SchObjectFactory(eImage, eCreate_Default);
  I.Location := PtXY(VD('x1', 0), VD('y1', 0));
  I.Corner := PtXY(VD('x2', 0), VD('y2', 0));
  I.FileName := V('file');
  I.EmbedImage := True;
  I.KeepAspect := True;
  SchDoc.RegisterSchObjectInContainer(I);
end;

{ ------------------------------------------------------------------ dispatcher }
procedure Exec(Cmd: String; Line: String);
begin
  if Cmd = 'S_PIN' then DoSPin
  else if Cmd = 'WIRE' then DoWire(False)
  else if Cmd = 'S_POLYLINE' then DoSPolyline
  else if Cmd = 'S_RECT' then DoSRect
  else if Cmd = 'C_PARAM' then DoCParam
  else if Cmd = 'C_BEGIN' then DoCBegin
  else if Cmd = 'C_END' then DoCEnd
  else if Cmd = 'C_DES' then DoCDes
  else if Cmd = 'C_COMMENT' then DoCComment
  else if Cmd = 'C_IMPL' then DoCImpl
  else if Cmd = 'JUNCTION' then DoJunction
  else if Cmd = 'NETLABEL' then DoNetLabel
  else if Cmd = 'POWER' then DoPower
  else if Cmd = 'NOERC' then DoNoErc
  else if Cmd = 'S_ARC' then DoSArc
  else if Cmd = 'S_CIRCLE' then DoSCircle
  else if Cmd = 'S_POLYGON' then DoSPolygon
  else if Cmd = 'S_BEZIER' then DoSBezier
  else if Cmd = 'S_LABEL' then DoSLabel
  else if Cmd = 'S_TEXTFRAME' then DoSTextFrame
  else if Cmd = 'BUS' then DoWire(True)
  else if Cmd = 'BUSENTRY' then DoBusEntry
  else if Cmd = 'SIGHARNESS' then DoSigHarness
  else if Cmd = 'HCONN' then DoHConn
  else if Cmd = 'HENTRY' then DoHEntry
  else if Cmd = 'HCONNEND' then DoHConnEnd
  else if Cmd = 'PORT' then DoPort
  else if Cmd = 'PARAMSET' then DoParamSet
  else if Cmd = 'SHEETSYM' then DoSheetSym
  else if Cmd = 'SHEETENTRY' then DoSheetEntry
  else if Cmd = 'SHEETSYMEND' then DoSheetSymEnd
  else if Cmd = 'IMAGE' then DoImage
  else if Cmd = 'DOCPARAM' then DoDocParam
  else if Cmd = 'TEMPLATE' then DoTemplate
  else if Cmd = 'SHIFT' then DoShift
  else if Cmd = 'L_COMP' then DoLComp
  else if Cmd = 'L_PARAM' then DoLParam
  else if Cmd = 'L_COMPEND' then DoLCompEnd
  else if Cmd = 'SCHDOC' then DoSchDoc
  else if Cmd = 'SAVESCH' then DoSaveSch
  else if Cmd = 'SCHLIB' then DoSchLib
  else if Cmd = 'SAVESCHLIB' then DoSaveSchLib
  else Err('unknown command ' + Cmd);
end;
