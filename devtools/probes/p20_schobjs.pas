function Pt(X, Y: Integer): TLocation;
begin
  Result := Point(MilsToCoord(X), MilsToCoord(Y));
end;

procedure Try_(Tag: String; Ok: Boolean);
begin
  if Ok then L(Tag + ' ok');
end;

procedure Body;
var Doc, SchDoc, LibDoc, Lib, C, P, R, W, J, NL, PP, Port, SS, SE, Par, Lbl, A, E, TF, Poly, BE, NoE, LC, FM: Variant;
    fid, i: Integer;
begin
  { ---------------- schematic library }
  try
    LibDoc := Client.OpenNewDocument('SCHLIB', '%ROOT%\devtools\_probe_run\p20.SchLib', '', False);
    Client.ShowDocument(LibDoc);
    Lib := SchServer.GetCurrentSchDocument;
    L('schlib open: ' + BoolToStr(Lib <> Nil, True));
    LC := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    LC.LibReference := 'DUAL_OPAMP';
    LC.ComponentDescription := 'probe dual';
    LC.Designator.Text := 'U?';
    LC.CurrentPartID := 1;
    LC.DisplayMode := 0;
    try LC.PartCount := 3; L('PartCount set'); except L('PartCount failed: ' + ExceptionMessage); end;
    for i := 1 to 2 do
    begin
      R := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
      R.Location := Pt(0, 0); R.Corner := Pt(400, 600);
      R.OwnerPartId := i; R.OwnerPartDisplayMode := 0;
      R.IsSolid := True; R.AreaColor := $C2FFFF; R.Color := $000084; R.LineWidth := eSmall;
      LC.AddSchObject(R);
      P := SchServer.SchObjectFactory(ePin, eCreate_Default);
      P.Location := Pt(0, 300); P.Orientation := eRotate180; P.PinLength := MilsToCoord(200);
      P.Designator := IntToStr(i); P.Name := 'IN' + IntToStr(i); P.Electrical := eElectricInput;
      P.OwnerPartId := i; P.OwnerPartDisplayMode := 0;
      LC.AddSchObject(P);
    end;
    P := SchServer.SchObjectFactory(ePin, eCreate_Default);
    P.Location := Pt(200, 600); P.Orientation := eRotate90; P.PinLength := MilsToCoord(200);
    P.Designator := '8'; P.Name := 'V+'; P.Electrical := eElectricPower; P.OwnerPartId := 0;
    P.IsHidden := True; P.HiddenNetName := 'VCC';
    LC.AddSchObject(P);
    Lib.AddSchComponent(LC);
    L('lib comp added, partcount now ' + IntToStr(LC.PartCount));
    LibDoc.DoFileSave('SchLib');
    L('schlib saved: ' + BoolToStr(FileExists('%ROOT%\devtools\_probe_run\p20.SchLib'), True));
  except
    L('SCHLIB failed: ' + ExceptionMessage);
  end;

  { ---------------- schematic document }
  Doc := CreateNewDocumentFromDocumentKind('SCH');
  SchDoc := SchServer.GetCurrentSchDocument;
  try
    SchDoc.UseCustomSheet := True;
    SchDoc.CustomX := MilsToCoord(11690); SchDoc.CustomY := MilsToCoord(8270);
    L('custom sheet ok');
  except L('sheet: ' + ExceptionMessage); end;
  try
    FM := SchServer.FontManager;
    fid := FM.GetFontID(8, 0, False, False, False, False, 'Arial');
    L('font id ' + IntToStr(fid));
  except L('font: ' + ExceptionMessage); end;
  try
    W := SchServer.SchObjectFactory(eWire, eCreate_Default);
    W.Location := Pt(1000, 1000);
    W.InsertVertex := 1; W.SetState_Vertex(1, Pt(1000, 1000));
    W.InsertVertex := 2; W.SetState_Vertex(2, Pt(2000, 1000));
    W.InsertVertex := 3; W.SetState_Vertex(3, Pt(2000, 1500));
    W.LineWidth := eSmall;
    SchDoc.RegisterSchObjectInContainer(W);
    L('wire ok vertices=' + IntToStr(W.VerticesCount));
  except L('wire: ' + ExceptionMessage); end;
  try
    J := SchServer.SchObjectFactory(eJunction, eCreate_Default);
    J.Location := Pt(1500, 1000); J.Locked := True;
    SchDoc.RegisterSchObjectInContainer(J); L('junction ok');
  except L('junction: ' + ExceptionMessage); end;
  try
    NL := SchServer.SchObjectFactory(eNetlabel, eCreate_Default);
    NL.Location := Pt(1200, 1000); NL.Text := 'NET_A'; NL.Orientation := eRotate0;
    NL.Justification := eJustify_BottomRight; NL.FontId := fid;
    SchDoc.RegisterSchObjectInContainer(NL); L('netlabel ok');
  except L('netlabel: ' + ExceptionMessage); end;
  try
    PP := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
    PP.Location := Pt(2000, 1500); PP.Text := 'GND'; PP.Style := ePowerGndPower; PP.Orientation := eRotate270;
    PP.ShowNetName := True;
    SchDoc.RegisterSchObjectInContainer(PP); L('power ok');
  except L('power: ' + ExceptionMessage); end;
  try
    Port := SchServer.SchObjectFactory(ePort, eCreate_Default);
    Port.Location := Pt(3000, 1000); Port.Name := 'SDA'; Port.IOType := ePortBidirectional;
    Port.Style := ePortRight; Port.Width := MilsToCoord(500); Port.Height := MilsToCoord(100);
    Port.Alignment := eHorizontalCentreAlign;
    SchDoc.RegisterSchObjectInContainer(Port); L('port ok');
  except L('port: ' + ExceptionMessage); end;
  try
    SS := SchServer.SchObjectFactory(eSheetSymbol, eCreate_Default);
    SS.Location := Pt(4000, 3000); SS.XSize := MilsToCoord(1000); SS.YSize := MilsToCoord(800);
    SS.SheetName.Text := 'Child';
    SS.SheetFileName.Text := 'child.SchDoc';
    SchDoc.RegisterSchObjectInContainer(SS);
    SE := SchServer.SchObjectFactory(eSheetEntry, eCreate_Default);
    SE.Name := 'SDA'; SE.Side := eLeftSide; SE.DistanceFromTop := MilsToCoord(200); SE.IOType := ePortBidirectional;
    SS.AddSchObject(SE);
    L('sheet symbol ok, entry loc ' + IntToStr(CoordToMils(SE.Location.X)) + ',' + IntToStr(CoordToMils(SE.Location.Y)));
  except L('sheet symbol: ' + ExceptionMessage); end;
  try
    Lbl := SchServer.SchObjectFactory(eLabel, eCreate_Default);
    Lbl.Location := Pt(1000, 3000); Lbl.Text := 'Free text'; Lbl.FontId := fid; Lbl.Orientation := eRotate90;
    Lbl.Justification := eJustify_Center;
    SchDoc.RegisterSchObjectInContainer(Lbl); L('label ok');
  except L('label: ' + ExceptionMessage); end;
  try
    A := SchServer.SchObjectFactory(eArc, eCreate_Default);
    A.Location := Pt(1500, 4000); A.Radius := MilsToCoord(200); A.StartAngle := 0; A.EndAngle := 135; A.LineWidth := eSmall;
    SchDoc.RegisterSchObjectInContainer(A); L('arc ok');
    E := SchServer.SchObjectFactory(eEllipse, eCreate_Default);
    E.Location := Pt(2500, 4000); E.Radius := MilsToCoord(100); E.SecondaryRadius := MilsToCoord(100); E.IsSolid := True;
    SchDoc.RegisterSchObjectInContainer(E); L('ellipse ok');
    Poly := SchServer.SchObjectFactory(ePolyline, eCreate_Default);
    Poly.InsertVertex := 1; Poly.SetState_Vertex(1, Pt(3000, 4000));
    Poly.InsertVertex := 2; Poly.SetState_Vertex(2, Pt(3300, 4300));
    Poly.LineStyle := eLineStyleDashed;
    SchDoc.RegisterSchObjectInContainer(Poly); L('polyline ok');
    BE := SchServer.SchObjectFactory(eBusEntry, eCreate_Default);
    BE.Location := Pt(3500, 2000); BE.Corner := Pt(3600, 2100);
    SchDoc.RegisterSchObjectInContainer(BE); L('bus entry ok');
    NoE := SchServer.SchObjectFactory(eNoERC, eCreate_Default);
    NoE.Location := Pt(3700, 2000);
    SchDoc.RegisterSchObjectInContainer(NoE); L('noerc ok');
    TF := SchServer.SchObjectFactory(eTextFrame, eCreate_Default);
    TF.Location := Pt(5000, 1000); TF.Corner := Pt(6000, 1500); TF.Text := 'Line1' + #13#10 + 'Line2'; TF.ShowBorder := True; TF.WordWrap := True;
    SchDoc.RegisterSchObjectInContainer(TF); L('textframe ok');
  except L('graphics: ' + ExceptionMessage); end;
  { component placed by geometry + state }
  try
    C := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
    C.LibReference := 'DUAL_OPAMP';
    C.SourceLibraryName := 'p20.SchLib';
    C.CurrentPartID := 2;
    C.DisplayMode := 0;
    C.Location := Pt(6000, 3000);
    SchDoc.RegisterSchObjectInContainer(C);
    C.Designator.Text := 'U1';
    C.Designator.Location := Pt(6000, 3700);
    C.Comment.Text := 'TL072';
    C.UniqueId := 'ABCDEFGH';
    Par := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    Par.Name := 'MPN'; Par.Text := 'TL072CDR'; Par.Location := Pt(6000, 2800); Par.IsHidden := False;
    C.AddSchObject(Par);
    L('component ok uid=' + C.UniqueId + ' part=' + IntToStr(C.CurrentPartID));
    try C.SetState_OrientationWithoutRotating(eRotate90); L('orient w/o rotating ok o=' + IntToStr(C.Orientation)); except L('OWR: ' + ExceptionMessage); end;
  except L('component: ' + ExceptionMessage); end;
  Doc.SetFileName('%ROOT%\devtools\_probe_run\p20.SchDoc');
  Doc.DoFileSave('');
  L('saved sch');
end;
